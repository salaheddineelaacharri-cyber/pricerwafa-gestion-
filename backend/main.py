"""
API FastAPI — courbe des taux et pricing oblig (calculs Python).
"""

from __future__ import annotations

import logging
import math
import os
import re
import sys
from collections.abc import Callable
from datetime import date, datetime, timedelta
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from yield_curve import CurveInputs, YieldCurve, bond_valuation_report, linear_interp_extrapolate

import obligation_amort_schedule as _obl_amort_mod

logger = logging.getLogger(__name__)

_oam_file = Path(_obl_amort_mod.__file__).resolve()
try:
    _oam_file.relative_to(_ROOT.resolve())
except ValueError as exc:
    raise RuntimeError(
        f"obligation_amort_schedule importé hors racine du dépôt : {_oam_file} "
        f"(racine attendue : {_ROOT.resolve()})"
    ) from exc
if _obl_amort_mod._NPV_HP_SUM_MARKER not in (_obl_amort_mod.PRICER_AMORT_ENGINE_ID or ""):
    raise RuntimeError(
        f"Fichier obligation_amort_schedule.py incorrect ou obsolète : {_oam_file} — "
        f"PRICER_AMORT_ENGINE_ID doit contenir « {_obl_amort_mod._NPV_HP_SUM_MARKER} » (NPV pleine précision). "
        f"Valeur actuelle : {_obl_amort_mod.PRICER_AMORT_ENGINE_ID!r}"
    )

PRICER_AMORT_ENGINE_ID = _obl_amort_mod.PRICER_AMORT_ENGINE_ID
appliquer_grille_amort_sur_lignes_marche = _obl_amort_mod.appliquer_grille_amort_sur_lignes_marche
construire_tables_amortissement_pour_valorisation = _obl_amort_mod.construire_tables_amortissement_pour_valorisation
diagnostic_feuilles_amortissement = _obl_amort_mod.diagnostic_feuilles_amortissement

from pricing.curves.zc_interpolation_excel import (
    NDIGITS_TAUX_SECONDAIRE_FORMULE_B_DEFAUT,
    taux_secondaire_interpole_formule_b,
    vba_interpolate,
)
from pricing.data_access import (
    SqlDataAccessError,
    charger_histo_courbe_taux,
    charger_referentiel_titre_codes,
    read_sql_dataframe,
)
from valuation_zc_obligations import (
    CHEMIN_BASE_TITRE_OBLIG_PREFERENTIEL,
    charger_base_titre_oblg,
    charger_courbe_zc_depuis_fichier,
    filtrer_dataframe_par_code_maroclear,
    interp_taux_secondaire_jours,
    ligne_code_maroclear_correspond,
    resoudre_fichier_base_titre_oblig,
    spread_decimal_arrondi_prime_pct3,
    valoriser_dataframe_base_titre,
)

# Piliers de référence marché (3m, 6m, 1a, 2a, 5a, 10a, 15a, ~19,5a) — figés dans le tableau type Excel
FIXED_BENCHMARK_DAYS: tuple[int, ...] = (91, 182, 365, 730, 1825, 3650, 5475, 7123)
# Début du bloc long en pas réguliers (jours), après la zone CT / piliers
START_LONG_GRID = 1300
# Maturités LT hors pas régulier (depuis 1300 j) — ex. ~29 ans et ~30 ans
EXTRA_GRID_DAYS: tuple[int, ...] = (10616, 10958)

# Cache léger en mémoire pour éviter des relectures Excel coûteuses à chaque requête.
_DF_BASE_CACHE: dict[str, tuple[float, int, pd.DataFrame]] = {}
_PRIX_MR_CACHE: dict[str, dict[str, float]] = {}
_AMORT_DIAG_CACHE: dict[str, tuple[float, int, dict]] = {}
_PRIX_MANARR_CACHE: dict[str, tuple[float, int, tuple[list[dict[str, Any]], str | None]]] = {}

def _schedule_annee_excel(maturity_days: float) -> float:
    """Colonne Année type Excel : M/365 si M<365 ; sinon entier ARRONDI(M/365,0) ≥ 1."""
    a = float(maturity_days)
    if a < 365.0:
        return a / 365.0
    return float(max(1, int(round(a / 365.0))))


def _calendar_year_spot_maturity_days(d0: date, *, start_n: int = 2, end_n: int = 30) -> list[float]:
    """Maturités en jours : (d0 + N années calendaires - d0).days pour N = start_n..end_n."""
    out: list[float] = []
    for n in range(int(start_n), int(end_n) + 1):
        delta = (d0 + relativedelta(years=n)) - d0
        out.append(float(delta.days))
    return out


def _zc_annual_schedule_maturity_days_from_histo(
    root: Path,
    date_courbe: str,
    courbe: str = "MAR_JJ",
) -> tuple[np.ndarray, dict[str, Any]]:
    """
    Grille « Échéancier annuel ZC » : maturités CT SQL (< 365 j) + point 1 an (365 j)
    + maturités (d0 + N cal. - d0).days pour N = 2..30.
    """
    pil = _extraire_piliers_depuis_histo(root, str(date_courbe).strip()[:10], courbe)
    d0 = datetime.fromisoformat(str(pil["date_used"])[:10]).date()
    ct_days = sorted(
        {
            float(s["maturity_days"])
            for s in pil["short"]
            if float(s["maturity_days"]) < 365.0
        }
    )
    year_spots = _calendar_year_spot_maturity_days(d0, start_n=2, end_n=30)
    merged = sorted({*ct_days, 365.0, *year_spots})
    days = np.asarray(merged, dtype=float)
    meta: dict[str, Any] = {
        "date_courbe": str(pil["date_used"]),
        "courbe": str(pil.get("courbe", courbe)),
        "ct_maturities_sql_lt_365": ct_days,
        "calendar_year_spots_N2_N30": year_spots,
        "final_maturity_days": days.tolist(),
    }
    return days, meta


def _schedule_table_records(
    curve: YieldCurve,
    *,
    root: Path | None = None,
    date_courbe: str | None = None,
    courbe: str = "MAR_JJ",
    maturity_days: np.ndarray | None = None,
) -> list[dict]:
    """
    Échéancier aligné Excel — relations :

    - **Taux** (b) : Formule A CT / transition / MLT (inchangé).

    - **Année (T)** : si M<365 → M/365 ; si M≥365 → ARRONDI(M/365;0) (entier 1…30).

    - **TauxZC** (z), décimal, puis affichage ARRONDI 4 dec en % :
        - M<365 : z = b (taux monétaire).
        - M=365 : z = B×365/360 = (1+365×B/360)−1 (taux zéro-coupon simple 1 an).
        - M>365 : bootstrap sur coupon par z avec somme des **PXZC** (facteurs d’actualisation décimaux
          des lignes précédentes M≥365) :
          z = ((1+B)/(1−B×SOMME(PXZC_précédents)))^(1/T) − 1.

    - **PXZC** : si M≥365, facteur 1/(1+z)^T (décimal ; l’UI affiche ×100 en %) ; sinon vide.

    - **TauxZCActuariel** (z_act), décimal, ARRONDI 8 dec en % :
        - M<365 : z_act = (1+B×M/360)^(365/M) − 1 (MM ACT/360 → actuariel annualisé base 365).
        - M≥365 : z_act = z (même taux que TauxZC avant arrondi d’affichage).

    **Maturités** : plus de liste figée ; ``maturity_days`` explicite ou construction
    via ``root`` + ``date_courbe`` (piliers CT ``histo_courbe_taux`` + 365 j + spots calendaires 2–30 ans).
    """
    if root is not None and (date_courbe or "").strip():
        days, meta_zc = _zc_annual_schedule_maturity_days_from_histo(
            root, date_courbe.strip()[:10], courbe
        )
        logger.debug(
            "zc_annual_schedule | date_courbe=%s | ct_sql_lt365=%s | calendar_N2_N30=%s | final=%s",
            meta_zc["date_courbe"],
            meta_zc["ct_maturities_sql_lt_365"],
            meta_zc["calendar_year_spots_N2_N30"],
            meta_zc["final_maturity_days"],
        )
    elif maturity_days is not None:
        days = np.asarray(maturity_days, dtype=float)
    else:
        raise ValueError(
            "_schedule_table_records : fournir maturity_days=... ou root=... et date_courbe=... "
            "(grille ZC annuelle dynamique)."
        )
    taux_ct = np.asarray(curve.money_market_rate(days), dtype=float)
    schedule_cap = float(np.max(days))
    l_d = np.asarray(getattr(curve, "_l_d", []), dtype=float)
    l_r = np.asarray(getattr(curve, "_l_R", []), dtype=float)
    s_d_curve = np.asarray(getattr(curve, "_s_d", []), dtype=float)
    joint_long_day = float(np.max(s_d_curve)) if s_d_curve.size > 0 else float(curve.joint) + 1.0
    joint_mm = float(curve.money_market_rate(np.array([joint_long_day], dtype=float)))
    try:
        joint_act = float(np.power(1.0 + joint_mm * joint_long_day / 360.0, 365.0 / joint_long_day) - 1.0)
        l_d_sched = np.concatenate([np.array([joint_long_day], dtype=float), l_d[l_d > joint_long_day]])
        l_r_sched = np.concatenate([np.array([joint_act], dtype=float), l_r[l_d > joint_long_day]])
        m_sched = l_d_sched <= schedule_cap
        if np.sum(m_sched) >= 2:
            taux_mlt = np.asarray(linear_interp_extrapolate(days, l_d_sched[m_sched], l_r_sched[m_sched]), dtype=float)
        else:
            taux_mlt = np.asarray(curve.long_actuarial_rate_for_schedule(days, schedule_cap), dtype=float)
    except Exception:
        taux_mlt = np.asarray(
            curve.long_actuarial_rate_for_schedule(days, schedule_cap), dtype=float
        )
    # Logique Taux pour l'échéancier (alignée classeur ``2026-PRICER_WG_CORRIGE``) :
    # - K <= G2 (MAX _mat1, ex. 326 / 255 / 192 selon la date) : ``interpoler(_mat1, taux1, K)`` ;
    # - K > 365         : ``interpoler(_mat2, taux2, K)`` — courbe actuarielle longue ;
    # - G2 < K <= 365  : Excel applique ``interpoler(_mat1, taux1, K)`` qui, via les
    #   cellules de queue (A49=G2, A50=premier_pilier_long ; B49=MM_joint,
    #   B50=MM_synthétique=((1+act_long)^(d_long/365)-1)*360/d_long), revient à une
    #   extrapolation **linéaire sur l'échelle monétaire** entre ``(joint, MM_joint)``
    #   et ``(premier_pilier_long, MM_synthétique)``. C'est ce comportement que l'on
    #   reproduit ici (et **non** une conversion act→mon directe d'un taux long
    #   interpolé sur l'échelle actuarielle, qui décalait ``Taux_ZC`` à partir de
    #   365 j et faisait diverger les prix 9424 / 9500 / 9351).
    base = 365.0
    taux_mixte = np.empty_like(days, dtype=float)
    mask_mlt = days > 365
    mask_ct = days <= float(joint_long_day)
    mask_trans = ~(mask_mlt | mask_ct)
    taux_mixte[mask_mlt] = taux_mlt[mask_mlt]
    taux_mixte[mask_ct] = taux_ct[mask_ct]
    if np.any(mask_trans):
        d_trans = days[mask_trans]
        last_short_d = float(joint_long_day)  # = MAX(_mat1) côté Excel (cellule G2)
        mm_last_short = float(joint_mm)
        first_long_d_arr = l_d[l_d > last_short_d] if l_d.size > 0 else np.array([], dtype=float)
        if first_long_d_arr.size > 0:
            first_long_d = float(first_long_d_arr[0])
            first_long_r = float(l_r[l_d > last_short_d][0])
            mm_first_long_synth = (
                np.power(1.0 + first_long_r, first_long_d / 365.0) - 1.0
            ) * 360.0 / first_long_d
            slope_excel = (mm_first_long_synth - mm_last_short) / (first_long_d - last_short_d)
            taux_mixte[mask_trans] = mm_last_short + slope_excel * (d_trans - last_short_d)
        else:
            # Pas de pilier long disponible : on retombe sur l'ancienne formule.
            r_trans = taux_mlt[mask_trans]
            taux_mixte[mask_trans] = (
                (np.power(1.0 + r_trans, d_trans / base) - 1.0) * 360.0 / d_trans
            )

    z_work = np.zeros_like(days, dtype=float)  # taux ZC décimal (précision bootstrap)
    z_actuariel = np.zeros_like(days, dtype=float)
    pxzc = np.full_like(days, fill_value=np.nan, dtype=float)
    annee_col = np.zeros_like(days, dtype=float)
    sum_pxzc_prev = 0.0

    for i in range(len(days)):
        a = float(days[i])
        b = float(taux_mixte[i])
        t_annee = _schedule_annee_excel(a)
        annee_col[i] = t_annee

        if a < 365.0:
            z = b
            z_act = np.power(1.0 + b * a / 360.0, 365.0 / a) - 1.0
        elif a == 365.0:
            t_boot = float(t_annee)  # = 1
            z = b * 365.0 / 360.0
            z_act = z
            px = 1.0 / np.power(1.0 + z, t_boot)
            pxzc[i] = px
            sum_pxzc_prev += px
        else:
            t_boot = float(t_annee)
            denom = 1.0 - b * sum_pxzc_prev
            if denom <= 0.0 or t_boot <= 0.0:
                z = b
            else:
                z = np.power((1.0 + b) / denom, 1.0 / t_boot) - 1.0
            z_act = z
            px = 1.0 / np.power(1.0 + z, t_boot)
            pxzc[i] = px
            sum_pxzc_prev += px

        z_work[i] = z
        z_actuariel[i] = z_act

    rows: list[dict] = []
    for i in range(len(days)):
        rows.append(
            {
                "Maturity_days": float(days[i]),
                "Taux_pct": round(float(taux_mixte[i]) * 100.0, 8),
                "Annee": float(annee_col[i]),
                "Taux_ZC_pct": round(float(z_work[i]) * 100.0, 4),
                # Même colonne TauxZC (décision bootstrap), précision pour le graphique / export.
                "Taux_ZC_pct_full": round(float(z_work[i]) * 100.0, 8),
                "PXZC": None if np.isnan(pxzc[i]) else float(pxzc[i]),
                "Taux_ZC_actuariel_pct": round(float(z_actuariel[i]) * 100.0, 8),
            }
        )
    return rows


def _interp_taux_zc_depuis_schedule_annuel(duree_annees: float, schedule_rows: list[dict]) -> float:
    """
    Taux ZC **décimal** : reproduit ``=interpoler(mat_zc;Taux_ZC;D209)`` d’Excel.

    - ``mat_zc`` = colonne **Année** de l’échéancier ZC (0.003, 0.145, …, 1, 2, 3 … 30).
    - ``duree_annees`` = **(date_tombée − date_valo) / 365** (années), comme la ligne durée hors REV
      dans ``obligation_amort_schedule``.

    Pour une durée négative (tombée passée) ``vba_interpolate`` renvoie le premier taux.
    """
    if not schedule_rows:
        return 0.0
    xs = np.array([float(r["Annee"]) for r in schedule_rows], dtype=float)
    ys = np.array([float(r["Taux_ZC_actuariel_pct"]) / 100.0 for r in schedule_rows], dtype=float)
    return float(vba_interpolate(xs, ys, float(duree_annees)))


def _interp_taux_zc_actuariel_depuis_schedule_jours(jours: float, schedule_rows: list[dict]) -> float:
    """
    Taux ZC **actuariel** (décimal), même colonne que l’UI « Échéancier annuel (ZC) » — **TauxZCActuariel** :

    interpolation type ``interpoler`` sur l’axe **Maturity_days** (jours) du schedule tracé, pas sur la colonne Année.
    L’argument ``jours`` est le nombre de jours calendaires (date tombée − date valorisation), aligné tableau BAM.
    """
    if not schedule_rows:
        return 0.0
    xs = np.array([float(r["Maturity_days"]) for r in schedule_rows], dtype=float)
    ys = np.array([float(r["Taux_ZC_actuariel_pct"]) / 100.0 for r in schedule_rows], dtype=float)
    return float(vba_interpolate(xs, ys, float(jours)))


def maturity_grid(max_days: float, step_short: int, step_long: int, joint: float) -> np.ndarray:
    """
    Grille type tableau d’interpolation :
    - 1 jour puis pas CT (ex. 50) jusqu’au seuil joint ;
    - piliers fixes 91, 182, 365, 730, 1825, 3650, 5475, 7123 ;
    - à partir de 1300 j : pas LT (ex. 100) jusqu’à max_days ;
    - 10616, 10958 j si couverts par max_days (hors grille 100 depuis 1300).
    """
    j = int(joint)
    max_d = int(max_days)
    pts: set[int] = set()
    pts.add(1)
    pts.update(range(step_short, min(j, max_d) + 1, step_short))
    if j <= max_d:
        pts.add(j)
    for d in FIXED_BENCHMARK_DAYS:
        if d <= max_d:
            pts.add(d)
    for x in range(START_LONG_GRID, max_d + 1, step_long):
        pts.add(x)
    for m in EXTRA_GRID_DAYS:
        if m <= max_d:
            pts.add(m)
    return np.array(sorted(pts), dtype=float)


def excel_style_maturity_order(
    days: np.ndarray, joint_days: float, step_short: int
) -> tuple[np.ndarray, list[str]]:
    """
    Ordre d’affichage type Excel : (1) CT au pas régulier, (2) piliers orange, (3) LT depuis 1300 j.
    Retourne les maturités et une étiquette de bloc par ligne : ct | pillar | long | other.
    """
    dset = {int(round(float(x))) for x in days}
    if not dset:
        return np.array([], dtype=float), []
    j = int(joint_days)
    dmax = max(dset)

    bench_set = set(FIXED_BENCHMARK_DAYS)
    block1: list[int] = []
    if 1 in dset:
        block1.append(1)
    for x in range(step_short, min(j, dmax) + 1, step_short):
        if x in dset and x not in bench_set:
            block1.append(x)
    if j in dset and j not in bench_set:
        block1.append(j)

    out1: list[int] = []
    seen: set[int] = set()
    for x in block1:
        if x not in seen:
            seen.add(x)
            out1.append(x)

    block2 = [b for b in FIXED_BENCHMARK_DAYS if b in dset]

    block3 = sorted(x for x in dset if x >= START_LONG_GRID and x not in bench_set)

    ordered = out1 + block2 + block3
    groups = ["ct"] * len(out1) + ["pillar"] * len(block2) + ["long"] * len(block3)

    missing = dset - set(ordered)
    if missing:
        extra = sorted(missing)
        ordered.extend(extra)
        groups.extend(["other"] * len(extra))

    return np.array(ordered, dtype=float), groups


class PillarShort(BaseModel):
    maturity_days: float = Field(gt=0)
    mm_rate_pct: float


class PillarLong(BaseModel):
    maturity_days: float = Field(gt=0)
    actuarial_rate_pct: float


class CurveRequest(BaseModel):
    short: list[PillarShort]
    long: list[PillarLong]
    joint_days: float = Field(default=325, gt=0)
    max_days: float = Field(default=11000, gt=0)
    step_short: int = Field(default=50, ge=1)
    step_long: int = Field(default=100, ge=1)
    zc_schedule_anchor_date: str | None = Field(
        default=None,
        description="AAAA-MM-JJ : ancrage calendrier de l'échéancier ZC annuel (CT SQL + 365 j + années 2–30). Requis pour /api/curve.",
    )


class CurvePillarsFromHistoRequest(BaseModel):
    """Extraction des piliers CT/LT depuis HISTO_COURBE_TAUX."""

    date_courbe: str = Field(description="Date de courbe (AAAA-MM-JJ)")
    courbe: str = Field(default="MAR_JJ", description="Valeur de la colonne COURBE")


class BondRequest(BaseModel):
    curve: CurveRequest
    nominal: float = Field(gt=0)
    coupon_pct: float = Field(ge=0)
    maturity_years: float = Field(gt=0)
    frequency: int = Field(ge=1, le=12)


class BaseTitreZcRequest(BaseModel):
    """Chemins relatifs à la racine du projet (optionnels)."""

    courbe_zc_py: str | None = Field(
        default=None,
        description="Module Python exposant COURBE_ZC (défaut: pricing/curves/courbe_zc.py)",
    )
    excel_xlsx: str | None = Field(
        default=None,
        description="Chemin optionnel relatif à la racine ; sinon data/obligations/base_titre_oblig.xlsx ou un seul base_titre*oblig*.xlsx à la racine",
    )


class MarcheValorizeRequest(BaseModel):
    """Pricing obligataire (UI) : même jeu de données que base-titre ZC, format lignes « marché »."""

    valuation_date: str | None = Field(default=None, description="Date de valorisation (AAAA-MM-JJ), pour échéance estimée si absente")
    code_maroclear: str | None = Field(default=None, description="Filtre optionnel sur CODE / Maroclear")
    courbe_zc_py: str | None = None
    excel_xlsx: str | None = None
    curve: CurveRequest | None = Field(
        default=None,
        description="Piliers CT/LT (comme « Tracer la courbe ») : taux secondaire interpolé Formule B + spread ; sinon seul courbe_zc.py",
    )
    feuil1_pricer_tous: bool = Field(
        default=False,
        description="Si True : remplit feuil1_titres (ordre Feuil1) avec Prix arrondi / Prix MR / écart issus du moteur pour chaque code.",
    )
    prix_manarr_pricer_tous: bool = Field(
        default=False,
        description="Si True : valorise les codes du fichier prix manarrr.xlsx dans l'ordre du fichier.",
    )


def _scalar(v):
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:
            pass
    return v


def _normaliser_filtre_maroclear_typo(s: str) -> tuple[str, str | None]:
    """
    Corrige les confusions fréquentes O (lettre) / 0 (chiffre) dans un code supposé numérique.
    Retourne (filtre_utilisé, astuce_si_changement) ; astuce None si inchangé.
    """
    t = (s or "").strip()
    if not t:
        return "", None
    if not re.fullmatch(r"[0-9Oo]{3,}", t):
        return t, None
    corr = t.replace("O", "0").replace("o", "0")
    if corr != t:
        return corr, f"Code interprété comme « {corr} » (lettre O remplacée par le chiffre 0)."
    return t, None


def _safe_float(x, default=0.0) -> float:
    try:
        if x is None:
            return default
        f = float(_scalar(x))
        if np.isnan(f) or np.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def _norm_txt_simple(s: str) -> str:
    return (
        str(s)
        .strip()
        .lower()
        .replace("é", "e")
        .replace("è", "e")
        .replace("ê", "e")
        .replace("ë", "e")
        .replace("à", "a")
        .replace("â", "a")
        .replace("ï", "i")
        .replace("î", "i")
        .replace("ô", "o")
        .replace("ù", "u")
        .replace("û", "u")
        .replace("ç", "c")
        .replace("_", " ")
    )


def _normaliser_code_simple(v) -> str:
    if v is None:
        return ""
    s = str(_scalar(v)).strip()
    if not s or s.lower() == "nan":
        return ""
    # Harmonisation Excel/Pandas: "9496.0" -> "9496"
    if re.fullmatch(r"[0-9]+\.0+", s):
        s = s.split(".", 1)[0]
    return s


def _titre_prix_manarr_est_code_maroclear_numerique(titre: str | None) -> bool:
    """
    True pour un code obligation Maroclear (ex. ``9538``, ``100844``).

    Exclut devises (USD, EURO, …) et ISIN (``XS…``) présents dans les classeurs Prix Manar.
    """
    code = _normaliser_code_simple(titre)
    return bool(code) and bool(re.fullmatch(r"[0-9]+", code))


def _filtrer_prix_manarr_codes_marocains(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if _titre_prix_manarr_est_code_maroclear_numerique(r.get("titre"))]


# Même nom que dans l’explorateur : ``base_titre_OBLG_.xlsx`` (souvent à la racine du projet, à côté de ``app.py``).
_OBLG_XLSX_NAME = "base_titre_OBLG_.xlsx"
_PRIX_MANARR_XLSX_NAME = "prix manarrr.xlsx"
_PRIX_MAR_XLSX_NAME = "prix mar.xlsx"
_PRIX_MOR_XLSX_NAME = "prix mor.xlsx"


def _resoudre_chemin_base_titre_oblg_xlsx(root: Path) -> Path | None:
    """
    Classeur **titre / valo** (Feuil1) : d’abord à la **racine du projet**, puis ``data/obligations/``.
    """
    for candidate in (root / _OBLG_XLSX_NAME, root / "data" / "obligations" / _OBLG_XLSX_NAME):
        p = candidate.resolve()
        if p.is_file():
            return p
    return None


def _resoudre_chemin_prix_manarr_xlsx(root: Path) -> Path | None:
    """Classeur racine ``prix manarrr.xlsx`` contenant le tableau titre/date/valo."""
    p = (root / _PRIX_MANARR_XLSX_NAME).resolve()
    return p if p.is_file() else None


def _resoudre_chemin_prix_mar_xlsx(root: Path) -> Path | None:
    """Classeur racine ``prix mar.xlsx`` contenant plusieurs dates Manar."""
    p = (root / _PRIX_MAR_XLSX_NAME).resolve()
    return p if p.is_file() else None


def _resoudre_chemin_prix_mor_xlsx(root: Path) -> Path | None:
    """Classeur racine ``prix mor.xlsx`` contenant plusieurs dates Manar."""
    p = (root / _PRIX_MOR_XLSX_NAME).resolve()
    return p if p.is_file() else None


def _chemin_excel_feuil1_prix_mr_oblg(root: Path) -> Path | None:
    """Fichier optionnel Feuil1 « titre / valo » (prioritaire pour Prix MR)."""
    return _resoudre_chemin_base_titre_oblg_xlsx(root)


def _feuil1_like_sheet_name(xlsx: Path) -> str | None:
    """Nom réel de la feuille (Feuil1 / Feuille1 / Sheet1), insensible à la casse et aux espaces."""
    try:
        xls = pd.ExcelFile(xlsx)
    except Exception:
        return None
    for sn in xls.sheet_names:
        n = _norm_txt_simple(str(sn)).replace(" ", "")
        if n in ("feuil1", "feuille1", "sheet1"):
            return str(sn)
    return None


def _parse_valo_cell_feuil1(v: Any) -> float:
    """Nombre Excel ou chaîne type « 8 342,07 » / « 8342,07 »."""
    if v is None:
        return float("nan")
    try:
        if pd.isna(v):
            return float("nan")
    except Exception:
        pass
    if isinstance(v, (int, np.integer)) and not isinstance(v, bool):
        f = float(v)
        return f if math.isfinite(f) else float("nan")
    if isinstance(v, float):
        return v if math.isfinite(v) else float("nan")
    s = str(_scalar(v)).strip().replace("\u00a0", " ")
    s = re.sub(r"\s+", "", s).replace(",", ".")
    try:
        f = float(s)
        return f if math.isfinite(f) else float("nan")
    except ValueError:
        return float("nan")


def _debug_prix_mr_stdout(root: Path, xlsx_valorisation: Path) -> None:
    """Logs détaillés si ``PRICER_DEBUG_PRIX_MR=1`` (ou true/yes)."""
    if os.environ.get("PRICER_DEBUG_PRIX_MR", "").strip().lower() not in ("1", "true", "yes"):
        return
    print("[pricer] DEBUG PRIX MR — début", flush=True)
    p_oblg_res = _resoudre_chemin_base_titre_oblg_xlsx(root)
    p_racine = (root / _OBLG_XLSX_NAME).resolve()
    p_data = (root / "data" / "obligations" / _OBLG_XLSX_NAME).resolve()
    print(f"[pricer] Excel OBLG_ (résolu) : {p_oblg_res}", flush=True)
    print(f"[pricer]   candidat racine : {p_racine} — existe={p_racine.is_file()}", flush=True)
    print(f"[pricer]   candidat data/ : {p_data} — existe={p_data.is_file()}", flush=True)
    print(f"[pricer] Classeur valorisation résolu : {xlsx_valorisation}", flush=True)
    for label, p in ("OBLG_ racine", p_racine), ("OBLG_ data/obligations", p_data), ("valorisation", xlsx_valorisation):
        if not p.is_file():
            continue
        sn_dbg = _feuil1_like_sheet_name(p)
        if not sn_dbg:
            print(f"[pricer] Aucune feuille Feuil1/Feuille1/Sheet1 dans ({label}): {p}", flush=True)
            continue
        try:
            df = pd.read_excel(p, sheet_name=sn_dbg)
        except Exception as e:
            print(f"[pricer] Feuil1 lecture ({label}): {e}", flush=True)
            continue
        print(f"[pricer] [{label}] Feuil1 OK — {len(df)} lignes, colonnes: {list(df.columns)}", flush=True)
        if len(df) > 0:
            print(f"[pricer] [{label}] 1re ligne: {df.iloc[0].to_dict()}", flush=True)
        code_test = 9580
        col_t = next((c for c in df.columns if _norm_txt_simple(str(c)).replace("-", " ") == "titre"), None)
        col_v = next((c for c in df.columns if _norm_txt_simple(str(c)).replace("-", " ") == "valo"), None)
        if col_t is None or col_v is None:
            print(f"[pricer] [{label}] colonnes titre/valo introuvables (titre={col_t!s}, valo={col_v!s})", flush=True)
            continue
        m = df[df[col_t].map(_normaliser_code_simple) == _normaliser_code_simple(code_test)]
        print(f"[pricer] [{label}] recherche {code_test}: {len(m)} ligne(s)", flush=True)
        if not m.empty:
            v0 = _parse_valo_cell_feuil1(m[col_v].values[0])
            print(f"[pricer] [{label}] valo trouvée: {v0}", flush=True)
        else:
            titres = [_normaliser_code_simple(x) for x in df[col_t].tolist() if _normaliser_code_simple(x)]
            print(f"[pricer] [{label}] titres (normalisés, extraits): {titres[:40]}", flush=True)
    m_comb = _charger_prix_mr_map_marche(root, xlsx_valorisation)
    print(f"[pricer] Carte Prix MR fusionnée (nb codes): {len(m_comb)} — ex. 9580 -> {m_comb.get('9580')}", flush=True)
    print("[pricer] DEBUG PRIX MR — fin", flush=True)


def _charger_prix_mr_map_marche(root: Path, xlsx: Path) -> dict[str, float]:
    """
    Feuil1 **titre → valo** : d’abord le classeur de valorisation, puis fusion avec
    ``base_titre_OBLG_.xlsx`` (racine projet ou ``data/obligations/``) si présent (**priorité** à ce dernier pour les codes communs).
    """
    out: dict[str, float] = dict(_charger_prix_mr_depuis_feuil1_titre_valo(xlsx))
    p_oblg = _chemin_excel_feuil1_prix_mr_oblg(root)
    if p_oblg is not None and p_oblg.resolve() != xlsx.resolve():
        out.update(_charger_prix_mr_depuis_feuil1_titre_valo(p_oblg))
    elif p_oblg is not None:
        out = _charger_prix_mr_depuis_feuil1_titre_valo(p_oblg)
    return out


def _charger_prix_mr_depuis_feuil1_titre_valo(xlsx: Path) -> dict[str, float]:
    """
    Règle unique : feuille **Feuil1** (ou Feuille1 / Sheet1) du classeur ``xlsx``,
    colonnes **titre** + **valo** → ``{CODE_NORMALISÉ → valo}`` pour **Prix MR**.
    Aucun filtre sur la date ; dernière ligne gagne si plusieurs lignes pour le même titre.
    """
    out: dict[str, float] = {}
    try:
        xls = pd.ExcelFile(xlsx)
    except Exception:
        return out
    sheet: str | None = None
    for sn in xls.sheet_names:
        n = _norm_txt_simple(sn).replace(" ", "")
        if n in ("feuil1", "feuille1", "sheet1"):
            sheet = sn
            break
    if not sheet:
        return out
    try:
        df = pd.read_excel(xlsx, sheet_name=sheet)
    except Exception:
        return out
    if df is None or df.empty:
        return out

    col_titre: str | None = None
    col_valo: str | None = None
    for c in df.columns:
        u = _norm_txt_simple(str(c)).replace("-", " ")
        if col_titre is None and u == "titre":
            col_titre = str(c)
    for c in df.columns:
        u = _norm_txt_simple(str(c)).replace("-", " ")
        if u == "valo" and str(c) != col_titre:
            col_valo = str(c)
            break
    if col_valo is None:
        for c in df.columns:
            u = _norm_txt_simple(str(c)).replace("-", " ")
            if ("valorisation" in u or "valo" in u) and str(c) != col_titre:
                col_valo = str(c)
                break
    if col_titre is None or col_valo is None:
        return out

    for _, r in df.iterrows():
        code = _normaliser_code_simple(r.get(col_titre))
        if not code:
            continue
        prix = _parse_valo_cell_feuil1(r.get(col_valo))
        if not math.isfinite(prix):
            continue
        out[code] = float(prix)
    return out


def _lire_feuil1_liste_titres(xlsx: Path) -> tuple[list[dict[str, Any]], str | None]:
    """
    Lit la feuille **Feuil1** (Feuille1 / Sheet1) ou **Referentiel_titre** (même schéma) :
    **titre** (code), **date**, **valo** (prix calculé), **PRICE** / prix Manar (**prix_mr**),
    **ecart** (sinon calcul : prix_mr − valo, comme la feuille de comparaison).
    """
    rows_out: list[dict[str, Any]] = []
    sheet_used: str | None = None
    try:
        xls = pd.ExcelFile(xlsx)
    except Exception:
        return rows_out, sheet_used
    if not xls.sheet_names:
        return rows_out, sheet_used

    def _norm_sn(sn: str) -> str:
        return _norm_txt_simple(sn).replace(" ", "")

    sheet: str | None = None
    for sn in xls.sheet_names:
        n = _norm_sn(sn)
        if n in ("feuil1", "feuille1", "sheet1"):
            sheet = sn
            break
    if not sheet:
        for sn in xls.sheet_names:
            nn = _norm_txt_simple(sn).replace(" ", "")
            if "referentiel" in nn and "titre" in nn:
                sheet = sn
                break
    if not sheet:
        return rows_out, sheet_used

    try:
        df = pd.read_excel(xlsx, sheet_name=sheet)
    except Exception:
        return rows_out, sheet_used
    if df is None or df.empty:
        return rows_out, sheet

    col_titre: str | None = None
    col_date: str | None = None
    col_valo: str | None = None
    col_prix_mr: str | None = None
    col_ecart: str | None = None
    for c in df.columns:
        u = _norm_txt_simple(str(c)).replace("-", " ")
        if col_titre is None and u == "titre":
            col_titre = str(c)
        if col_date is None and u == "date":
            col_date = str(c)
        if col_prix_mr is None:
            if u == "price" or u == "prix mr" or ("prix" in u and "mr" in u) or "manar" in u:
                col_prix_mr = str(c)
        if col_ecart is None and u == "ecart":
            col_ecart = str(c)
    # Colonne « valo » : préférer l’intitulé exact « valo » à « valorisation ».
    for c in df.columns:
        u = _norm_txt_simple(str(c)).replace("-", " ")
        if u == "valo" and str(c) != col_prix_mr:
            col_valo = str(c)
            break
    if col_valo is None:
        for c in df.columns:
            u = _norm_txt_simple(str(c)).replace("-", " ")
            if ("valorisation" in u or "valo" in u) and str(c) != col_prix_mr:
                col_valo = str(c)
                break
    if col_titre is None or col_valo is None:
        return rows_out, sheet

    sheet_used = sheet

    def _fmt_date(v: Any) -> str | None:
        try:
            if v is None or pd.isna(v):
                return None
        except Exception:
            if v is None:
                return None
        if isinstance(v, float) and (math.isnan(v) or np.isnan(v)):
            return None
        if hasattr(v, "strftime"):
            try:
                return v.strftime("%d/%m/%Y")
            except Exception:
                return str(v).strip() or None
        s = str(v).strip()
        return s if s and s.lower() not in ("nan", "nat", "none") else None

    def _float_cell(v: Any) -> float | None:
        f = _safe_float(v, float("nan"))
        return float(f) if math.isfinite(f) else None

    for _, r in df.iterrows():
        raw_t = r.get(col_titre)
        if raw_t is None or (isinstance(raw_t, float) and (math.isnan(raw_t) or np.isnan(raw_t))):
            continue
        titre = str(_scalar(raw_t)).strip()
        if not titre or titre.lower() == "nan":
            continue
        d_cell = r.get(col_date) if col_date else None
        valo_js = _float_cell(r.get(col_valo))
        prix_mr_js: float | None = _float_cell(r.get(col_prix_mr)) if col_prix_mr else None
        ecart_js: float | None = None
        if col_ecart:
            ecart_js = _float_cell(r.get(col_ecart))
        if ecart_js is None and prix_mr_js is not None and valo_js is not None:
            ecart_cents = int(round(prix_mr_js * 100.0)) - int(round(valo_js * 100.0))
            ecart_js = float(ecart_cents) / 100.0
        rows_out.append(
            {
                "titre": titre,
                "date": _fmt_date(d_cell),
                "valo": valo_js,
                "prix_mr": prix_mr_js,
                "ecart": ecart_js,
            }
        )
    return rows_out, sheet_used


def _date_key_prix_manar(v: Any) -> str | None:
    """Normalise une date Excel/API en ``YYYY-MM-DD`` pour filtrer ``prix mar.xlsx``."""
    try:
        if v is None or pd.isna(v):
            return None
    except Exception:
        if v is None:
            return None
    if hasattr(v, "date") and not isinstance(v, str):
        try:
            return v.date().isoformat()
        except Exception:
            pass
    if hasattr(v, "strftime") and not isinstance(v, str):
        try:
            return v.strftime("%Y-%m-%d")
        except Exception:
            pass
    s = str(_scalar(v)).strip()
    if not s or s.lower() in ("nan", "nat", "none"):
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:10], fmt).date().isoformat()
        except ValueError:
            continue
    try:
        dt = pd.to_datetime(s, dayfirst=True, errors="coerce")
        if pd.isna(dt):
            return None
        return dt.date().isoformat()
    except Exception:
        return None


def _lire_prix_manarr_table(root: Path, valuation_date: str | None = None) -> tuple[list[dict[str, Any]], str | None]:
    """
    Lit le tableau ``Prix Manar``.

    - Si ``prix mar.xlsx`` contient la date de valorisation demandée, ses lignes sont utilisées
      dans l'ordre du classeur (ex. 02/01/2026, 06/03/2026).
    - Sinon on conserve la source historique ``prix manarrr.xlsx`` (ex. contrôle 26/03/2026).
    """
    requested_date_key = _date_key_prix_manar(valuation_date)
    if requested_date_key:
        for prix_date in (
            _resoudre_chemin_prix_mar_xlsx(root),
            _resoudre_chemin_prix_mor_xlsx(root),
        ):
            if prix_date is None:
                continue
            rows_date, sheet_date = _lire_prix_manarr_table_depuis_xlsx(
                prix_date,
                prefer_prix_sheet=False,
                source_label=prix_date.name,
            )
            rows_for_date = [
                r for r in rows_date if _date_key_prix_manar(r.get("date_iso") or r.get("date")) == requested_date_key
            ]
            if rows_for_date:
                for r in rows_for_date:
                    r.pop("date_iso", None)
                return _filtrer_prix_manarr_codes_marocains(rows_for_date), sheet_date

    xlsx = _resoudre_chemin_prix_manarr_xlsx(root)
    if xlsx is None:
        return [], None
    rows_fallback, sheet_fallback = _lire_prix_manarr_table_depuis_xlsx(
        xlsx,
        prefer_prix_sheet=True,
        source_label=xlsx.name,
    )
    for r in rows_fallback:
        r.pop("date_iso", None)
    return _filtrer_prix_manarr_codes_marocains(rows_fallback), sheet_fallback


def _lire_prix_manarr_table_depuis_xlsx(
    xlsx: Path,
    *,
    prefer_prix_sheet: bool,
    source_label: str,
) -> tuple[list[dict[str, Any]], str | None]:
    """Lit un classeur contenant au minimum code/date/valo pour la section ``Prix Manar``."""
    try:
        stat = xlsx.stat()
    except OSError:
        return [], None
    key = str(xlsx.resolve())
    cached = _PRIX_MANARR_CACHE.get(key)
    if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
        rows_cached, sheet_cached = cached[2]
        return deepcopy(rows_cached), sheet_cached

    rows_out: list[dict[str, Any]] = []
    sheet_used: str | None = None
    try:
        xls = pd.ExcelFile(xlsx)
    except Exception:
        _PRIX_MANARR_CACHE[key] = (stat.st_mtime, stat.st_size, (rows_out, sheet_used))
        return rows_out, sheet_used
    if not xls.sheet_names:
        _PRIX_MANARR_CACHE[key] = (stat.st_mtime, stat.st_size, (rows_out, sheet_used))
        return rows_out, sheet_used

    sheet = xls.sheet_names[0]
    if prefer_prix_sheet:
        for sn in xls.sheet_names:
            n = _norm_txt_simple(str(sn)).replace(" ", "")
            if "prix" in n and ("manar" in n or "manarr" in n):
                sheet = sn
                break
    else:
        for sn in xls.sheet_names:
            n = _norm_txt_simple(str(sn)).replace(" ", "")
            if n in ("feuil1", "feuille1", "sheet1"):
                sheet = sn
                break

    try:
        df = pd.read_excel(xlsx, sheet_name=sheet)
    except Exception:
        _PRIX_MANARR_CACHE[key] = (stat.st_mtime, stat.st_size, (rows_out, sheet_used))
        return rows_out, sheet_used
    if df is None or df.empty:
        _PRIX_MANARR_CACHE[key] = (stat.st_mtime, stat.st_size, (rows_out, sheet))
        return rows_out, sheet

    col_titre: str | None = None
    col_date: str | None = None
    col_valo: str | None = None
    for c in df.columns:
        u = _norm_txt_simple(str(c)).replace("-", " ").strip()
        if col_titre is None and u == "titre":
            col_titre = str(c)
        if col_date is None and u in ("date", "date analyse", "date courbe"):
            col_date = str(c)
        if col_valo is None and u == "valo":
            col_valo = str(c)
    if col_titre is None or col_valo is None:
        _PRIX_MANARR_CACHE[key] = (stat.st_mtime, stat.st_size, (rows_out, sheet))
        return rows_out, sheet

    def _fmt_date(v: Any) -> str | None:
        try:
            if v is None or pd.isna(v):
                return None
        except Exception:
            if v is None:
                return None
        if hasattr(v, "strftime"):
            try:
                return v.strftime("%d/%m/%Y")
            except Exception:
                return str(v).strip() or None
        s = str(v).strip()
        return s if s and s.lower() not in ("nan", "nat", "none") else None

    for _, r in df.iterrows():
        code = _normaliser_code_simple(r.get(col_titre))
        if not code:
            continue
        prix = _parse_valo_cell_feuil1(r.get(col_valo))
        date_raw = r.get(col_date) if col_date else None
        rows_out.append(
            {
                "titre": code,
                "date": _fmt_date(date_raw) if col_date else None,
                "date_iso": _date_key_prix_manar(date_raw) if col_date else None,
                "valo": float(prix) if math.isfinite(prix) else None,
            }
        )

    sheet_used = f"{source_label}:{sheet}"
    _PRIX_MANARR_CACHE[key] = (stat.st_mtime, stat.st_size, (deepcopy(rows_out), sheet_used))
    return rows_out, sheet_used


def _codes_referentiel_depuis_df(df: pd.DataFrame) -> set[str]:
    """Extrait explicitement la colonne SQL ``code`` du référentiel chargé."""
    if df is None or df.empty:
        return set()
    col_code: str | None = None
    for c in df.columns:
        if str(c).strip().upper() == "CODE":
            col_code = str(c)
            break
    if col_code is None:
        for c in df.columns:
            u = str(c).strip().upper()
            if u.endswith(".CODE") or u.endswith("_CODE") or u.endswith(" CODE"):
                col_code = str(c)
                break
    if col_code is None or col_code not in df.columns:
        return set()
    return {_normaliser_code_simple(v) for v in df[col_code].tolist() if _normaliser_code_simple(v)}


def _valeur_colonne_insensible(row: pd.Series | None, *names: str) -> str:
    if row is None:
        return ""
    wanted = {n.strip().upper() for n in names}
    for c in row.index:
        if str(c).strip().upper() in wanted:
            v = row.get(c)
            if v is None:
                return ""
            s = str(v).strip()
            return "" if s.lower() in ("nan", "nat", "none") else s
    return ""


def _profil_metier_prix_manarr(row: pd.Series | None) -> str:
    if row is None:
        return "profil inconnu"
    parts = [
        _valeur_colonne_insensible(row, "TYPE_TAUX"),
        _valeur_colonne_insensible(row, "METHODE_VALO"),
        _valeur_colonne_insensible(row, "PERIODICITE_COUPON", "PERIODICITE_COUPO"),
        _valeur_colonne_insensible(row, "PERIODICITE_REMBOU"),
        _valeur_colonne_insensible(row, "BASE_CALCUL"),
        _valeur_colonne_insensible(row, "CATEGORIE"),
    ]
    cleaned = [p.strip().upper() for p in parts if p and p.strip()]
    return "/".join(cleaned) if cleaned else "profil inconnu"


def _df_subset_codes_ordre(df_in: pd.DataFrame, col_code: str, codes_ordre: list[str]) -> pd.DataFrame:
    """Une ligne par code, dans l’ordre de ``codes_ordre`` (ex. colonne Feuil1)."""
    rows: list[Any] = []
    for c in codes_ordre:
        s = str(c).strip()
        if not s:
            continue
        m = df_in[col_code].apply(lambda v, sb=s: ligne_code_maroclear_correspond(v, sb))
        sub = df_in.loc[m]
        if not sub.empty:
            rows.append(sub.iloc[0])
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def _valoriser_prix_manarr_rows(
    prix_rows: list[dict[str, Any]],
    df_in: pd.DataFrame,
    *,
    rows_ui_reference: list[dict[str, Any]] | None,
    col_code_fichier: str | None,
    courbe: dict[float, float],
    req: MarcheValorizeRequest,
    bam_cc: dict[float, float] | None,
    bam_cl: dict[float, float] | None,
    xlsx: Path,
    prix_mr_map: dict[str, float],
    fn_taux_zc_schedule: Callable[[float], float] | None,
    fn_taux_zc_schedule_a: Callable[[float], float] | None,
) -> list[dict[str, Any]]:
    """Valorise les lignes Prix Manar dans l'ordre du fichier et ajoute Prix arrondi / ecart."""
    if not prix_rows:
        return prix_rows
    if not col_code_fichier or col_code_fichier not in df_in.columns:
        return [
            {
                **r,
                "prix_arrondi": None,
                "ecart_prix_arrondi_valo": None,
                "source_prix_arrondi": "code non valorise",
                "source_ecart": "prix manquant",
                "ecart_a_corriger": True,
                "profil_metier": "profil inconnu",
            }
            for r in prix_rows
        ]

    out: list[dict[str, Any]] = []
    total = len(prix_rows)
    prix_reference: dict[str, float] = {}
    for rr in rows_ui_reference or []:
        pa_ref = _safe_float(rr.get("Prix arrondi"), float("nan"))
        if not math.isfinite(pa_ref):
            continue
        val_f = float(pa_ref)
        for cn in (
            _normaliser_code_simple(rr.get("CODE")),
            _obl_amort_mod._normaliser_code(rr.get("CODE")),
        ):
            if cn:
                prix_reference[cn] = val_f
    print(f"[Prix Manar] debut valorisation de {total} code(s)", flush=True)
    for idx, row in enumerate(prix_rows, start=1):
        code = _normaliser_code_simple(row.get("titre"))
        print(f"[Prix Manar] {idx}/{total} valorisation CODE {code or '?'}", flush=True)
        prix_arrondi: float | None = None
        ecart: float | None = None
        source_prix = "non valorise"
        profil_metier = "profil inconnu"
        if code:
            df_code = pd.DataFrame()
            try:
                mask = df_in[col_code_fichier].apply(lambda v, c=code: ligne_code_maroclear_correspond(v, c))
                df_code = df_in.loc[mask].head(1).copy()
                if not df_code.empty:
                    profil_metier = _profil_metier_prix_manarr(df_code.iloc[0])
            except Exception:
                df_code = pd.DataFrame()
            pa_direct = prix_reference.get(code)
            if pa_direct is None:
                cn_am = _obl_amort_mod._normaliser_code(row.get("titre"))
                if cn_am:
                    pa_direct = prix_reference.get(cn_am)
            if pa_direct is not None and math.isfinite(float(pa_direct)):
                prix_arrondi = round(float(pa_direct), 2)
                source_prix = "table Valorisation"
                valo = _safe_float(row.get("valo"), float("nan"))
                if math.isfinite(valo):
                    ecart = round(float(prix_arrondi) - float(valo), 2)
                print(
                    f"[Prix Manar] {idx}/{total} CODE {code} termine depuis table Valorisation | profil={profil_metier}",
                    flush=True,
                )
            else:
                if df_code.empty:
                    source_prix = "absent referentiel_titre"
                    print(f"[Prix Manar] {idx}/{total} CODE {code} absent du referentiel_titre", flush=True)
                else:
                    try:
                        rows_calc = _valoriser_slice_feuil1_batch(
                            df_code,
                            courbe=courbe,
                            req=req,
                            bam_cc=bam_cc,
                            bam_cl=bam_cl,
                            xlsx=xlsx,
                            prix_mr_map=prix_mr_map,
                            fn_taux_zc_schedule=fn_taux_zc_schedule,
                            fn_taux_zc_schedule_a=fn_taux_zc_schedule_a,
                            col_code_fichier=col_code_fichier,
                        )
                        if rows_calc:
                            pa = _safe_float(rows_calc[0].get("Prix clean"), float("nan"))
                            if not math.isfinite(pa):
                                pa = _safe_float(rows_calc[0].get("Prix arrondi"), float("nan"))
                            if math.isfinite(pa):
                                prix_arrondi = round(float(pa), 2)
                                source_prix = "recalcul complet"
                                valo = _safe_float(row.get("valo"), float("nan"))
                                if math.isfinite(valo):
                                    ecart = round(float(prix_arrondi) - float(valo), 2)
                        print(f"[Prix Manar] {idx}/{total} CODE {code} termine | profil={profil_metier}", flush=True)
                    except Exception as e:
                        print(
                            f"[Prix Manar] {idx}/{total} CODE {code} erreur: {type(e).__name__}: {e}",
                            flush=True,
                        )
                        source_prix = f"erreur {type(e).__name__}"
        ecart_a_corriger = True
        if ecart is None:
            source_ecart = "prix manquant"
        elif abs(float(ecart)) <= 0.02:
            ecart_a_corriger = False
            source_ecart = f"acceptable (tolérance ±0,02) | {profil_metier}"
        else:
            source_ecart = f"a corriger ({source_prix}) | {profil_metier}"
        out.append(
            {
                **row,
                "prix_arrondi": prix_arrondi,
                "ecart_prix_arrondi_valo": ecart,
                "source_prix_arrondi": source_prix,
                "source_ecart": source_ecart,
                "ecart_a_corriger": ecart_a_corriger,
                "profil_metier": profil_metier,
            }
        )
    print("[Prix Manar] fin valorisation", flush=True)
    return out


def _enrichir_prix_manarr_depuis_rows_ui(
    prix_manarr_rows: list[dict[str, Any]],
    rows_ui: list[dict[str, Any]],
) -> None:
    """
    Renseigne ``prix_arrondi`` / écart pour chaque ligne Prix Manar dont le ``titre`` correspond
    à un CODE présent dans ``rows_ui`` (prix **après** verrou grille = même montant que Valorisation).

    Sans cela, seul le mode « valoriser toutes les obligations » remplissait ``prix_arrondi`` via
    ``_valoriser_prix_manarr_rows`` ; une valorisation filtrée laissait la colonne vide.
    """
    by_code: dict[str, dict[str, Any]] = {}
    for r in rows_ui:
        for cn in (
            _normaliser_code_simple(r.get("CODE")),
            _obl_amort_mod._normaliser_code(r.get("CODE")),
        ):
            if cn:
                by_code[cn] = r
    for row in prix_manarr_rows:
        code = _normaliser_code_simple(row.get("titre"))
        cn_am = _obl_amort_mod._normaliser_code(row.get("titre"))
        src = by_code.get(code) if code else None
        if src is None and cn_am:
            src = by_code.get(cn_am)
        if not src:
            continue
        pa = _safe_float(src.get("Prix arrondi"), float("nan"))
        if not math.isfinite(pa):
            continue
        prix_arrondi = round(float(pa), 2)
        row["prix_arrondi"] = prix_arrondi
        row["source_prix_arrondi"] = "table Valorisation"
        valo = _safe_float(row.get("valo"), float("nan"))
        ecart: float | None = None
        if math.isfinite(valo):
            ecart = round(float(prix_arrondi) - float(valo), 2)
        row["ecart_prix_arrondi_valo"] = ecart
        profil = str(row.get("profil_metier") or "profil inconnu")
        if ecart is None:
            row["source_ecart"] = "prix manquant"
            row["ecart_a_corriger"] = True
        elif abs(float(ecart)) <= 0.02:
            row["source_ecart"] = f"acceptable (tolérance ±0,02) | {profil}"
            row["ecart_a_corriger"] = False
        else:
            row["source_ecart"] = f"a corriger (table Valorisation) | {profil}"
            row["ecart_a_corriger"] = True


def _appliquer_prix_mr_map_sur_lignes_marche(rows_ui: list[dict[str, Any]], prix_mr_map: dict[str, float]) -> None:
    """
    **Prix MR** = **valo** Excel (Feuil1 : ``titre`` == CODE) si présent dans ``prix_mr_map`` ;
    sinon **None** (et pas d’écart).
    """
    for r in rows_ui:
        code_n = _normaliser_code_simple(r.get("CODE"))
        pmr_sheet: float | None = None
        if prix_mr_map and code_n:
            raw = prix_mr_map.get(code_n)
            if raw is not None and math.isfinite(float(raw)):
                pmr_sheet = float(raw)

        if r.get("_marche_ligne_amortissable"):
            pd = _safe_float(r.get("Prix dirty"), float("nan"))
            if math.isfinite(pd):
                r["Prix arrondi"] = round(pd, 6)
                if pmr_sheet is not None:
                    pmr_2 = round(pmr_sheet + 0.0, 2)
                    r["Prix MR"] = pmr_2
                    pa_2 = round(float(r["Prix arrondi"]), 2)
                    ecart_cents = int(round(pa_2 * 100.0)) - int(round(pmr_2 * 100.0))
                    r["Ecart Prix arrondi - Prix MR"] = float(ecart_cents) / 100.0
                else:
                    r["Prix MR"] = None
                    r["Ecart Prix arrondi - Prix MR"] = None
            else:
                r["Prix MR"] = None
                r["Ecart Prix arrondi - Prix MR"] = None
            continue

        if pmr_sheet is not None:
            pmr_2 = round(pmr_sheet + 0.0, 2)
            r["Prix MR"] = pmr_2
            pa = _safe_float(r.get("Prix arrondi"), 0.0)
            pa_2 = round(pa + 0.0, 2)
            ecart_cents = int(round(pa_2 * 100.0)) - int(round(pmr_2 * 100.0))
            r["Ecart Prix arrondi - Prix MR"] = float(ecart_cents) / 100.0
            continue

        r["Prix MR"] = None
        r["Ecart Prix arrondi - Prix MR"] = None


def _appliquer_prix_mr_depuis_table_titre_valo(
    rows_ui: list[dict[str, Any]], feuil1_titres: list[dict[str, Any]]
) -> None:
    """
    Source unique pour Prix MR (vue marché):
    - table front ``titre/date/Prix arrondi/valo(Prix MR)/Écart``
    - correspondance stricte par code normalisé : ``titre == CODE``.
    """
    map_titre_valo: dict[str, float] = {}
    for t in feuil1_titres:
        code_t = _normaliser_code_simple(t.get("titre"))
        if not code_t:
            continue
        valo_t = _safe_float(t.get("valo"), float("nan"))
        if not math.isfinite(valo_t):
            continue
        map_titre_valo[code_t] = float(valo_t)

    for r in rows_ui:
        code_n = _normaliser_code_simple(r.get("CODE"))
        if not code_n:
            r["Prix MR"] = None
            r["Ecart Prix arrondi - Prix MR"] = None
            continue

        pmr = map_titre_valo.get(code_n)
        if pmr is None:
            r["Prix MR"] = None
            r["Ecart Prix arrondi - Prix MR"] = None
            continue

        pmr_2 = round(pmr, 2)
        r["Prix MR"] = pmr_2
        pa = _safe_float(r.get("Prix arrondi"), 0.0)
        pa_2 = round(pa, 2)
        ecart_cents = int(round(pa_2 * 100.0)) - int(round(pmr_2 * 100.0))
        r["Ecart Prix arrondi - Prix MR"] = float(ecart_cents) / 100.0


def _colonne_titre_feuil1(df: pd.DataFrame) -> str | None:
    """Colonne code titre : ``titre`` (prioritaire), sinon ``code`` / Maroclear."""
    for c in df.columns:
        u = _norm_txt_simple(str(c)).replace("-", " ").strip()
        if u == "titre":
            return str(c)
    for c in df.columns:
        u = _norm_txt_simple(str(c)).replace("-", " ").strip()
        if u in ("code", "code maroclear", "maroclear", "code_titre", "code titre"):
            return str(c)
    return None


def _colonne_valo_feuil1(df: pd.DataFrame, col_titre: str | None) -> str | None:
    """Colonne valo : intitulé exact ``valo``, sinon ``valorisation`` (hors colonne titre)."""
    for c in df.columns:
        if col_titre is not None and str(c) == col_titre:
            continue
        u = _norm_txt_simple(str(c)).replace("-", " ").strip()
        if u == "valo":
            return str(c)
    for c in df.columns:
        if col_titre is not None and str(c) == col_titre:
            continue
        u = _norm_txt_simple(str(c)).replace("-", " ").strip()
        if "valorisation" in u or u == "valo":
            return str(c)
    return None


def _prix_mr_map_depuis_feuil1_dataframe(df: pd.DataFrame) -> dict[str, float]:
    """Construit ``code normalisé → valo`` depuis un DataFrame déjà lu (feuille type Feuil1)."""
    out: dict[str, float] = {}
    if df is None or df.empty:
        return out
    col_titre = _colonne_titre_feuil1(df)
    col_valo = _colonne_valo_feuil1(df, col_titre)
    if col_titre is None or col_valo is None:
        return out
    for _, r in df.iterrows():
        code = _normaliser_code_simple(r.get(col_titre))
        if not code:
            continue
        prix = _parse_valo_cell_feuil1(r.get(col_valo))
        if math.isfinite(prix):
            out[code] = float(prix)
    return out


def lire_prix_mr_excel(
    code: Any,
    *,
    prix_map: dict[str, float] | None = None,
    df: pd.DataFrame | None = None,
    excel_path: Path | None = None,
) -> float | None:
    """
    Lit la valeur **valo** pour un code (colonne **titre**) sur **Feuil1** de ``base_titre_OBLG_.xlsx``.

    Priorité : ``prix_map`` (déjà construit), sinon ``df`` (Feuil1 déjà chargée), sinon lecture disque
    (``excel_path`` ou chemin projet par défaut).
    """
    try:
        m = prix_map
        if m is None and df is not None:
            m = _prix_mr_map_depuis_feuil1_dataframe(df)
        if m is None:
            p = excel_path if excel_path is not None else _resoudre_chemin_base_titre_oblg_xlsx(_ROOT)
            if p is None or not p.is_file():
                c1, c2 = _ROOT / _OBLG_XLSX_NAME, _ROOT / "data" / "obligations" / _OBLG_XLSX_NAME
                print(f"[pricer] Fichier Excel non trouvé: {c1} ni {c2}", flush=True)
                return None
            try:
                sn = _feuil1_like_sheet_name(p)
                if not sn:
                    m = _charger_prix_mr_depuis_feuil1_titre_valo(p)
                else:
                    df2 = pd.read_excel(p, sheet_name=sn)
                    m = _prix_mr_map_depuis_feuil1_dataframe(df2)
                    if not m:
                        m = _charger_prix_mr_depuis_feuil1_titre_valo(p)
            except Exception as e:
                print(f"[pricer] Erreur lecture Feuil1 ({p}): {e}", flush=True)
                m = _charger_prix_mr_depuis_feuil1_titre_valo(p)
        if not m:
            return None
        cn = _normaliser_code_simple(code)
        if not cn:
            return None
        prix = m.get(cn)
        if prix is not None and math.isfinite(float(prix)):
            return float(prix)
        if os.environ.get("PRICER_DEBUG_PRIX_MR", "").strip().lower() in ("1", "true", "yes"):
            print(f"[pricer] Code {cn!s} non trouvé dans Feuil1 (titre/valo)", flush=True)
        return None
    except Exception as e:
        print(f"[pricer] Erreur lecture Excel Prix MR: {e}", flush=True)
        return None


def _charger_prix_mr_oblg_feuil1_prioritaire(root: Path) -> dict[str, float]:
    """
    Carte titre → valo : lecture **Feuil1** en priorité (``sheet_name='Feuil1'``), sinon logique multi-feuilles
    existante (Feuille1 / Sheet1).
    """
    p = _resoudre_chemin_base_titre_oblg_xlsx(root)
    if p is None:
        c1, c2 = root / _OBLG_XLSX_NAME, root / "data" / "obligations" / _OBLG_XLSX_NAME
        print(f"[pricer] Fichier Excel non trouvé: {c1} ni {c2}", flush=True)
        return {}
    sn = _feuil1_like_sheet_name(p)
    if sn:
        try:
            df_f1 = pd.read_excel(p, sheet_name=sn)
            d1 = _prix_mr_map_depuis_feuil1_dataframe(df_f1)
            if d1:
                return d1
        except Exception as e:
            print(f"[pricer] Lecture feuille type Feuil1 ({p}): {e}", flush=True)
    return _charger_prix_mr_depuis_feuil1_titre_valo(p)


def _forcer_prix_mr_depuis_feuil1_oblg_xlsx(root: Path, rows_ui: list[dict[str, Any]]) -> None:
    """
    Lecture **directe** de ``base_titre_OBLG_.xlsx`` (racine ou ``data/obligations/`` ; feuille Feuil1 : titre → valo)
    et mise à jour de « Prix MR » / écart pour chaque ligne, **après** le reste du pipeline.
    """
    d_oblg = _charger_prix_mr_oblg_feuil1_prioritaire(root)
    if not d_oblg:
        return
    for ligne in rows_ui:
        code = ligne.get("CODE")
        prix_mr_excel = lire_prix_mr_excel(code, prix_map=d_oblg)
        if prix_mr_excel is not None:
            pmr_2 = round(float(prix_mr_excel), 2)
            ligne["Prix MR"] = pmr_2
            pa = _safe_float(ligne.get("Prix arrondi"), 0.0)
            pa_2 = round(pa + 0.0, 2)
            ecart_cents = int(round(pa_2 * 100.0)) - int(round(pmr_2 * 100.0))
            ligne["Ecart Prix arrondi - Prix MR"] = float(ecart_cents) / 100.0


def _valoriser_slice_feuil1_batch(
    df_slice: pd.DataFrame,
    *,
    courbe: dict[float, float],
    req: MarcheValorizeRequest,
    bam_cc: dict[float, float] | None,
    bam_cl: dict[float, float] | None,
    xlsx: Path,
    prix_mr_map: dict[str, float],
    fn_taux_zc_schedule: Callable[[float], float] | None,
    fn_taux_zc_schedule_a: Callable[[float], float] | None,
    col_code_fichier: str,
) -> list[dict[str, Any]]:
    """Même pipeline que la valorisation marché (grille + ATP + Prix MR) sur un sous-ensemble de lignes base titre."""
    if df_slice.empty:
        return []
    df_out, det_slice = valoriser_dataframe_base_titre(
        df_slice,
        courbe,
        valuation_date=req.valuation_date,
        bam_courbe_court=bam_cc,
        bam_courbe_long=bam_cl,
        progress_label="Valorisation Prix Manar",
    )
    raw_l = _df_to_records(df_out)
    rows_ui = [_row_to_marche_ui(r, req.valuation_date) for r in raw_l]
    amort_tables: list[dict] = []
    try:
        if bam_cc is not None and bam_cl is not None and len(bam_cc) >= 1 and len(bam_cl) >= 1:

            def _ts_amort(j: float) -> float:
                return float(
                    taux_secondaire_interpole_formule_b(
                        float(j),
                        bam_cc,
                        bam_cl,
                        ndigits=NDIGITS_TAUX_SECONDAIRE_FORMULE_B_DEFAUT,
                    )
                )
        else:

            def _ts_amort(j: float) -> float:
                return float(interp_taux_secondaire_jours(float(j), courbe))

        amort_tables = construire_tables_amortissement_pour_valorisation(
            xlsx,
            raw_l,
            rows_ui,
            valuation_date=req.valuation_date,
            taux_secondaire_a_j=_ts_amort,
            taux_zc_schedule_j=fn_taux_zc_schedule,
            taux_zc_schedule_a=fn_taux_zc_schedule_a,
            df_work=df_slice,
            col_code_fichier=col_code_fichier,
            det_cols=det_slice,
            codes_filter=[_normaliser_code_simple(r.get("CODE")) for r in rows_ui],
        )
        appliquer_grille_amort_sur_lignes_marche(rows_ui, amort_tables)
    except Exception:
        amort_tables = []
    codes_skip: set[str] = set()
    for t in amort_tables:
        try:
            pa = t.get("prix_actualise")
            if (
                pa is not None
                and math.isfinite(float(pa))
                and float(pa) > 0
                and bool(t.get("appliquer_prix_echeancier"))
            ):
                c_am = _obl_amort_mod._normaliser_code(t.get("code"))
                if c_am:
                    codes_skip.add(c_am)
                c_sm = _normaliser_code_simple(t.get("code"))
                if c_sm:
                    codes_skip.add(c_sm)
        except (TypeError, ValueError):
            pass
    _reappliquer_brut_atp_sur_lignes_ui(rows_ui, raw_l, codes_skip)
    _appliquer_prix_mr_map_sur_lignes_marche(rows_ui, prix_mr_map)
    for r in rows_ui:
        r.pop("_marche_ligne_amortissable", None)
    return rows_ui


def _cache_key_fichier(path: Path) -> tuple[str, float, int]:
    p = str(path.resolve())
    if str(path).replace("\\", "/").startswith("__sql_server__/"):
        return p, 0.0, 0
    st = path.stat()
    return p, st.st_mtime, st.st_size


def _charger_base_titre_oblg_cache(
    xlsx: Path,
    codes_filter: list[str] | tuple[str, ...] | set[str] | None = None,
) -> pd.DataFrame:
    p, mt, sz = _cache_key_fichier(xlsx)
    codes_n: list[str] = []
    if codes_filter:
        for code in codes_filter:
            cn = _normaliser_code_simple(code)
            if cn and cn not in codes_n:
                codes_n.append(cn)
    cache_key = p if not codes_n else f"{p}::codes={','.join(codes_n)}"
    c = _DF_BASE_CACHE.get(cache_key)
    if c and c[0] == mt and c[1] == sz:
        # Copie défensive: le flux de valorisation ne doit jamais muter l'objet en cache.
        return c[2].copy(deep=True)
    if codes_n:
        df = charger_referentiel_titre_codes(codes_n)
        df.columns = [str(c).strip() for c in df.columns]
    else:
        df = charger_base_titre_oblg(xlsx)
    _DF_BASE_CACHE[cache_key] = (mt, sz, df.copy(deep=True))
    return df


def _pr_mr_cache_key_marche(root: Path, xlsx: Path) -> str:
    """Clé cache = concat des (chemin, mtime, size) des fichiers impliqués (invalidation si modif)."""
    parts: list[tuple[str, float, int]] = [_cache_key_fichier(xlsx)]
    p2 = _chemin_excel_feuil1_prix_mr_oblg(root)
    if p2 is not None and p2.resolve() != xlsx.resolve():
        parts.append(_cache_key_fichier(p2))
    elif p2 is not None:
        parts = [_cache_key_fichier(p2)]
    return "|".join(f"{a}#{b}#{c}" for a, b, c in parts)


def _charger_prix_mr_cache_marche(root: Path, xlsx: Path) -> dict[str, float]:
    ck = _pr_mr_cache_key_marche(root, xlsx)
    c = _PRIX_MR_CACHE.get(ck)
    if c is not None:
        return dict(c)
    out = _charger_prix_mr_map_marche(root, xlsx)
    _PRIX_MR_CACHE[ck] = dict(out)
    return dict(out)


def get_valo_depuis_excel(
    code_maroclear: str,
    *,
    racine: Path | None = None,
    excel_xlsx: str | None = None,
) -> float | None:
    """
    Lit **valo** sur **Feuil1** (Feuille1 / Sheet1) lorsque **titre** == code Maroclear (normalisé).

    Le classeur est résolu via ``resoudre_fichier_base_titre_oblig`` (ex. ``data/obligations/base_titre_OBLG_.xlsx``).
    """
    root = racine if racine is not None else _ROOT
    try:
        xlsx = resoudre_fichier_base_titre_oblig(root, excel_xlsx)
    except Exception:
        return None
    m = _charger_prix_mr_cache_marche(root, xlsx)
    c = _normaliser_code_simple(code_maroclear)
    if not c:
        return None
    v = m.get(c)
    if v is None or not math.isfinite(float(v)):
        return None
    return float(v)


def get_valo_simple(
    code: str,
    *,
    racine: Path | None = None,
    excel_xlsx: str | None = None,
) -> float | None:
    """Lit **valo** sur **Feuil1** lorsque **titre** == ``code`` (normalisé). Même classeur que la valorisation."""
    return get_valo_depuis_excel(code, racine=racine, excel_xlsx=excel_xlsx)


def _diagnostic_feuilles_amortissement_cache(xlsx: Path) -> dict:
    p, mt, sz = _cache_key_fichier(xlsx)
    c = _AMORT_DIAG_CACHE.get(p)
    if c and c[0] == mt and c[1] == sz:
        return deepcopy(c[2])
    out = diagnostic_feuilles_amortissement(xlsx)
    _AMORT_DIAG_CACHE[p] = (mt, sz, deepcopy(out))
    return out


def _nominal_depuis_ligne_marche(d: dict) -> float:
    """Nominal pour l’UI : clés calculées puis colonnes type Excel (NOMINAL, VN, encours…)."""
    for key in ("nominal_pricing", "nominal_valo"):
        x = _safe_float(d.get(key), 0.0)
        if x > 0:
            return x
    for k, v in d.items():
        ku = (
            str(k)
            .strip()
            .upper()
            .replace("É", "E")
            .replace("È", "E")
            .replace("Ê", "E")
        )
        if not ku or "TAUX" in ku:
            continue
        if ku in ("NOMINAL", "VN", "VM", "ENCOURS", "MONTANT NOMINAL", "NOMINAL NET"):
            x = _safe_float(v, 0.0)
            if x > 0:
                return x
        if ku.startswith("NOMINAL"):
            x = _safe_float(v, 0.0)
            if x > 0:
                return x
    return 0.0


def _row_to_marche_ui(d: dict, valuation_date: str | None) -> dict:
    """Mappe une ligne issue de ``valoriser_dataframe_base_titre`` vers le format attendu par le front."""
    code = None
    for k, v in d.items():
        if str(k).strip().upper() == "CODE":
            code = _scalar(v)
            break
    if code is None:
        for k, v in d.items():
            sk = str(k).lower()
            if "maroclear" in sk.replace("é", "e"):
                code = _scalar(v)
                break
    if code is None:
        for k, v in d.items():
            if str(k).strip().lower() == "code":
                code = _scalar(v)
                break
    if code is None:
        code = ""
    if isinstance(code, float) and np.isfinite(code) and abs(code - round(code)) < 1e-9:
        code = int(round(code))

    desc = ""
    for k, v in d.items():
        if str(k).strip().upper() == "DESCRIPTION":
            desc = str(v) if v is not None and str(v) not in ("nan", "None") else ""
            break
    if not desc:
        for k, v in d.items():
            if str(k).strip().upper() in ("LIB_COURT", "LIB_COUR", "LIBELLE_COURT"):
                desc = str(v) if v is not None and str(v) not in ("nan", "None") else ""
                break
    if not desc:
        for k, v in d.items():
            ku = str(k).strip().upper().replace("É", "E")
            if ku in ("NOM_VALEUR", "NOM VALEUR"):
                desc = str(v) if v is not None and str(v) not in ("nan", "None") else ""
                break
    if not desc:
        for k, v in d.items():
            sk = str(k).lower().replace("é", "e")
            if "nom" in sk and "valeur" in sk:
                desc = str(v) if v is not None and str(v) not in ("nan", "None") else ""
                break
    if not desc:
        for k, v in d.items():
            sk = str(k).lower()
            if "libell" in sk:
                desc = str(v) if v is not None and str(v) != "nan" else ""
                break
    if not desc:
        for k, v in d.items():
            if "description" in str(k).lower():
                desc = str(v) if v is not None and str(v) != "nan" else ""
                break

    tc_dec = _safe_float(d.get("taux_coupon_decimal"), 0.0)
    prix_d = _safe_float(d.get("prix_dirty"), 0.0)
    ytm = _safe_float(d.get("ytm"), 0.0)
    dmod = _safe_float(d.get("duration_modifiee"), 0.0)
    d_mac = _safe_float(d.get("duration_macaulay"), 0.0)
    cx = _safe_float(d.get("convexite"), 0.0)
    matj = _safe_float(d.get("maturite_residuelle_jours"), 0.0)
    if matj <= 0:
        for k, v in d.items():
            lk = str(k).lower()
            if "residuel" in lk and "jour" in lk:
                matj = _safe_float(v, 0.0)
                break

    coupon_couru = _safe_float(d.get("coupon_courru_atp"), 0.0)
    # ATP : prix clean explicite du moteur ; ZC : pas de coupon couru → clean = dirty.
    p_atp = d.get("prix_clean_atp")
    if p_atp is not None and math.isfinite(float(_safe_float(p_atp, float("nan")))):
        prix_clean = _safe_float(p_atp, prix_d - coupon_couru)
    else:
        prix_clean = prix_d - coupon_couru
    # Sensibilité = duration modifiée (comme fiche titre Excel) : D_Macaulay / (1 + YTM), YTM en décimal (ex. 2,985 % → 0,02985).
    den_ytm = 1.0 + ytm
    if d_mac > 0 and abs(den_ytm) > 1e-15 and math.isfinite(ytm):
        sens = d_mac / den_ytm
    elif dmod > 0:
        sens = dmod
    else:
        sens = 0.0

    date_ech = ""
    for k, v in d.items():
        lk = str(k).lower().replace("é", "e")
        if str(k).strip().upper().replace(" ", "_") == "DATE_ECHEANCE":
            if v is not None and str(v).strip() and str(v).lower() != "nan":
                date_ech = str(v).split(" ")[0].split("T")[0]
            break
    if not date_ech:
        for k, v in d.items():
            lk = str(k).lower().replace("é", "e")
            if "echeance" in lk and "emis" not in lk and v is not None and str(v).strip() and str(v).lower() != "nan":
                date_ech = str(v).split(" ")[0].split("T")[0]
                break

    if not date_ech and valuation_date and matj > 0:
        try:
            dt = datetime.fromisoformat(str(valuation_date)[:10])
            date_ech = (dt + timedelta(days=int(round(matj)))).strftime("%d/%m/%Y")
        except Exception:
            pass

    nominal_ui = _nominal_depuis_ligne_marche(d)

    spread_dec_ui = spread_decimal_arrondi_prime_pct3(_safe_float(d.get("spread_decimal_valo"), 0.0))
    date_emis = ""
    de_raw = d.get("date_emission_iso")
    if de_raw is not None and str(de_raw).strip() and str(de_raw).lower() != "nan":
        date_emis = str(de_raw).strip()
    if not date_emis:
        for k, v in d.items():
            lk = str(k).lower().replace("é", "e")
            if ("emission" in lk or "émission" in str(k).lower()) and "echeance" not in lk:
                if v is not None and str(v).strip() and str(v).lower() != "nan":
                    date_emis = str(v).split(" ")[0].split("T")[0]
                    break

    moteur = d.get("moteur_prix")
    if moteur is not None and str(moteur).strip().lower() in ("nan", "none", ""):
        moteur = ""
    return {
        "CODE": code,
        "description": desc,
        "moteur_prix": str(moteur).strip() if moteur is not None else "",
        # Taux facial réellement retenu pour le coupon couru (normalisé WG si ATP).
        "Taux facial utilisé (coupon couru)": round(tc_dec * 100.0, 4),
        "TAUX": round(tc_dec * 100.0, 6),
        "Date d'échéance": date_ech,
        "Maturité résiduelle (jours)": int(round(matj)),
        "Coupon couru": coupon_couru,
        "Prix dirty": round(float(prix_d), 6) if math.isfinite(float(prix_d)) else prix_d,
        "Prix clean": round(float(prix_clean), 6) if math.isfinite(float(prix_clean)) else prix_clean,
        # Prix **clean** affiché : 6 décimales après la virgule (ex. 109 326,290229).
        "Prix arrondi": round(float(prix_clean), 6) if math.isfinite(float(prix_clean)) else prix_clean,
        "Rendement (YTM)": round(float(ytm) + 1e-15, 5) if math.isfinite(float(ytm)) else ytm,
        # Excel : duration titre = Macaulay ; sensibilité = Macaulay / (1 + YTM).
        "Duration titre": d_mac if d_mac > 0 else dmod,
        "Sensibilité": round(float(sens), 6) if math.isfinite(float(sens)) else sens,
        "Convexité": cx,
        "Nominal": round(float(nominal_ui), 2) if math.isfinite(float(nominal_ui)) else 0.0,
        "Description": desc,
        # Spread en points de % (ex. 0,49 pour 0,490 % issu de 49 centièmes).
        "Spread": round(float(spread_dec_ui) * 100.0, 3)
        if math.isfinite(float(spread_dec_ui))
        else spread_dec_ui,
        "Date d'émission": date_emis,
    }


def _reappliquer_brut_atp_sur_lignes_ui(
    rows_ui: list[dict],
    raw: list[dict],
    codes_avec_grille_amortissement: set[str] | None = None,
) -> None:
    """
    La grille d’amortissement peut écraser « Coupon couru » / prix avec des montants issus du
    référentiel (ex. 5,599 %). Les lignes **ATP** sont la référence WG : on réinjecte depuis
    ``raw`` (sortie DataFrame valorisation) **après** ``appliquer_grille_amort_sur_lignes_marche``.

    Si un titre a une **grille d’amortissement** avec prix NPV (oblig amortissable), on ne réinjecte
    pas le prix ATP : la somme des flux actualisés de l’échéancier fait foi pour l’UI.
    """
    skip_atp_prix = codes_avec_grille_amortissement or set()
    atp_metrics_only_codes: set[str] = set()
    by_code: dict[str, dict] = {}
    for r in raw:
        cn = _normaliser_code_simple(r.get("CODE"))
        if cn:
            by_code[cn] = r
        cn2 = _obl_amort_mod._normaliser_code(r.get("CODE"))
        if cn2 and cn2 != cn:
            by_code[cn2] = r
    for row in rows_ui:
        cn = _normaliser_code_simple(row.get("CODE"))
        cn_am = _obl_amort_mod._normaliser_code(row.get("CODE"))
        src = by_code.get(cn) or (by_code.get(cn_am) if cn_am else None)
        if not src:
            continue
        atp_flag = str(src.get("moteur_prix") or "").strip().upper() == "ATP"
        pca = src.get("prix_clean_atp")
        pcf0 = _safe_float(pca, float("nan")) if pca is not None else float("nan")
        has_atp_prix = pca is not None and math.isfinite(pcf0)
        # Lignes ATP : ``moteur_prix`` ou, à défaut, présence d’un prix clean ATP (non renseigné en ZC).
        if not atp_flag and not has_atp_prix:
            continue
        skip_price_only = cn in skip_atp_prix or (cn_am and cn_am in skip_atp_prix)
        if skip_price_only and cn not in atp_metrics_only_codes and (not cn_am or cn_am not in atp_metrics_only_codes):
            continue
        ytm_src = _safe_float(src.get("ytm"), float("nan"))
        dmod_src = _safe_float(src.get("duration_modifiee"), 0.0)
        dmac_src = _safe_float(src.get("duration_macaulay"), 0.0)
        cx_src = _safe_float(src.get("convexite"), 0.0)
        if dmac_src > 0 and math.isfinite(ytm_src) and abs(1.0 + ytm_src) > 1e-15:
            sens_src = dmac_src / (1.0 + ytm_src)
        elif dmod_src > 0:
            sens_src = dmod_src
        else:
            sens_src = 0.0
        if math.isfinite(ytm_src):
            row["Rendement (YTM)"] = round(float(ytm_src), 5)
        if dmac_src > 0:
            row["Duration titre"] = round(float(dmac_src), 6)
        elif dmod_src > 0:
            row["Duration titre"] = round(float(dmod_src), 6)
        if math.isfinite(float(sens_src)):
            row["Sensibilité"] = round(float(sens_src), 6)
        if math.isfinite(float(cx_src)):
            row["Convexité"] = round(float(cx_src), 6)
        matj_src = _safe_float(src.get("maturite_residuelle_jours"), 0.0)
        if matj_src > 0:
            row["Maturité résiduelle (jours)"] = int(round(matj_src))
        if skip_price_only:
            continue
        cc = src.get("coupon_courru_atp")
        if cc is not None:
            ccf = _safe_float(cc, float("nan"))
            if math.isfinite(ccf):
                row["Coupon couru"] = round(ccf, 4)
        pc = src.get("prix_clean_atp")
        if pc is not None:
            pcf = _safe_float(pc, float("nan"))
            if math.isfinite(pcf):
                row["Prix clean"] = round(pcf, 6)
                row["Prix arrondi"] = round(pcf, 6)
                ccy = _safe_float(row.get("Coupon couru"), 0.0)
                row["Prix dirty"] = round(pcf + ccy, 6)


def _make_curve(req: CurveRequest) -> YieldCurve:
    if not req.short or not req.long:
        raise HTTPException(status_code=400, detail="Au moins un pilier CT et un pilier LT sont requis.")
    s = np.array([[p.maturity_days, p.mm_rate_pct / 100.0] for p in req.short], dtype=float)
    l = np.array([[p.maturity_days, p.actuarial_rate_pct / 100.0] for p in req.long], dtype=float)
    # Excel G2 = dernier jour _mat1 : joint_days = G2 − 1 (cohérent avec les piliers CT chargés).
    joint_eff = float(np.max(s[:, 0])) - 1.0 if s.shape[0] else float(req.joint_days)
    inp = CurveInputs(
        short_maturities_days=s[:, 0],
        short_mm_rates=s[:, 1],
        long_maturities_days=l[:, 0],
        long_actuarial_rates=l[:, 1],
        joint_days=joint_eff,
    )
    return YieldCurve(inp)


def _courbes_bam_depuis_requete(req: CurveRequest) -> tuple[dict[float, float], dict[float, float]]:
    """Piliers BAM CT/LT avec le point LT synthétique Excel au dernier jour CT (MAX _mat1 = G2)."""
    bam_cc = {float(p.maturity_days): float(p.mm_rate_pct) / 100.0 for p in req.short}
    bam_cl = {float(p.maturity_days): float(p.actuarial_rate_pct) / 100.0 for p in req.long}
    if bam_cc:
        try:
            joint_long_day = float(max(float(k) for k in bam_cc.keys()))
            xs = np.array(sorted(float(k) for k in bam_cc.keys()), dtype=float)
            ys = np.array([float(bam_cc[float(k)]) for k in xs], dtype=float)
            r_mm = float(vba_interpolate(xs, ys, joint_long_day))
            r_act = float(np.power(1.0 + r_mm * joint_long_day / 360.0, 365.0 / joint_long_day) - 1.0)
            bam_cl[joint_long_day] = r_act
        except Exception:
            pass
    return bam_cc, bam_cl


def _df_to_records(df: pd.DataFrame) -> list[dict]:
    return df.replace({np.nan: None}).to_dict(orient="records")


def _extraire_piliers_depuis_histo(root: Path, date_courbe: str, courbe: str) -> dict[str, Any]:
    try:
        df = charger_histo_courbe_taux(courbe)
    except SqlDataAccessError as e:
        raise HTTPException(status_code=503, detail=f"SQL Server indisponible: {e}") from e
    if df is None or df.empty:
        raise HTTPException(status_code=404, detail="dbo.histo_courbe_taux est vide.")

    cols = {str(c).strip().upper(): str(c) for c in df.columns}
    required = ("COURBE", "DATE_COURBE", "VALEUR_MATURITE", "VALEUR_TAUX")
    missing = [k for k in required if k not in cols]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Colonnes manquantes dans dbo.histo_courbe_taux: {', '.join(missing)}",
        )

    c_courbe = cols["COURBE"]
    c_date = cols["DATE_COURBE"]
    c_mat = cols["VALEUR_MATURITE"]
    c_taux = cols["VALEUR_TAUX"]

    dfx = df.copy()
    dfx[c_courbe] = dfx[c_courbe].astype(str).str.strip()
    dfx[c_date] = pd.to_datetime(dfx[c_date], errors="coerce").dt.date
    dfx[c_mat] = pd.to_numeric(dfx[c_mat], errors="coerce")
    dfx[c_taux] = pd.to_numeric(dfx[c_taux], errors="coerce")
    dfx = dfx.dropna(subset=[c_date, c_mat, c_taux])

    courbe_norm = (courbe or "MAR_JJ").strip() or "MAR_JJ"
    dfx = dfx[dfx[c_courbe] == courbe_norm]
    if dfx.empty:
        raise HTTPException(status_code=404, detail=f"Aucune ligne trouvée pour COURBE={courbe_norm}.")

    try:
        date_wanted = datetime.fromisoformat(str(date_courbe)[:10]).date()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"date_courbe invalide: {date_courbe}") from e

    dates = sorted(dfx[c_date].unique().tolist())
    if not dates:
        raise HTTPException(status_code=404, detail="Aucune date disponible pour cette courbe.")
    if date_wanted not in set(dates):
        raise HTTPException(
            status_code=404,
            detail=(
                f"Aucun taux BAM n'est disponible pour la date {date_wanted.strftime('%d/%m/%Y')} "
                f"dans dbo.histo_courbe_taux (courbe {courbe_norm})."
            ),
        )
    date_used = date_wanted

    split_maturity_days = 365.0
    dfd = dfx[dfx[c_date] == date_used].drop_duplicates(subset=[c_mat], keep="last").sort_values(c_mat)
    points: list[dict[str, Any]] = []
    for _, row in dfd.iterrows():
        maturity = float(row[c_mat])
        rate = float(row[c_taux])
        if not math.isfinite(maturity) or not math.isfinite(rate) or maturity <= 0:
            continue
        maturity_json: int | float = int(maturity) if maturity.is_integer() else maturity
        points.append(
            {
                "maturity_days": maturity_json,
                "rate_pct": rate,
                "segment": "CT" if maturity <= split_maturity_days else "LT",
            }
        )

    short = [
        {"maturity_days": p["maturity_days"], "mm_rate_pct": p["rate_pct"]}
        for p in points
        if p["segment"] == "CT"
    ]
    long = [
        {"maturity_days": p["maturity_days"], "actuarial_rate_pct": p["rate_pct"]}
        for p in points
        if p["segment"] == "LT"
    ]
    if not short or not long:
        raise HTTPException(
            status_code=400,
            detail="La courbe SQL doit contenir au moins un point CT (<=365j) et un point LT (>365j).",
        )
    max_ct = max(float(p["maturity_days"]) for p in short)
    joint_days = max_ct - 1.0
    return {
        "short": short,
        "long": long,
        "joint_days": joint_days,
        "joint_long_day": max_ct,
        "points": points,
        "split_maturity_days": split_maturity_days,
        "max_maturity_days": max(float(p["maturity_days"]) for p in points),
        "date_requested": date_wanted.isoformat(),
        "date_used": date_used.isoformat(),
        "courbe": courbe_norm,
        "source_file": "dbo.histo_courbe_taux",
    }


app = FastAPI(title="Pricer — Courbe des taux", version="1.0.0")

# Middleware **tout de suite** après création de l’app (ordre recommandé FastAPI / Starlette).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _log_loaded_main_py() -> None:
    """Visible dans le terminal uvicorn : confirme quel fichier ``main.py`` est exécuté."""
    p = Path(__file__).resolve()
    print(f"[pricer] backend.main.py chargé : {p}", flush=True)
    route_paths = {p for p in (getattr(r, "path", None) for r in app.routes) if p}
    if "/api/pricer-meta" in route_paths:
        print("[pricer] route OK : /api/pricer-meta enregistrée", flush=True)
    else:
        print("[pricer] ERREUR : /api/pricer-meta absente — process Python obsolète ?", flush=True)


@app.get("/api/health")
def health():
    """
    ``atp_taux_facial_2dec`` : True si ce process charge ``pricing_atp`` avec la normalisation
    du taux coupon (5,599 % → 5,60 %). Si False après déploiement, le serveur n’a pas été relancé.

    ``amort_engine`` : chemin + mtime du module ``obligation_amort_schedule`` réellement chargé ;
    ``amort_engine_id`` doit correspondre à la dernière version du code (sinon : pas de reload).
    """
    try:
        import pricing_atp as _pa

        snap = hasattr(_pa, "_normalise_taux_coupon_annuel_wg_deux_dec_pct")
    except Exception:
        snap = False
    try:
        am_path = Path(_obl_amort_mod.__file__).resolve()
        am_mtime = am_path.stat().st_mtime
    except Exception:
        am_path, am_mtime = None, None
    payload = {
        "status": "ok",
        "health_format": 2,
        "atp_taux_facial_2dec": snap,
        "project_root": str(_ROOT.resolve()),
        # Aide au debug « mauvais serveur » : si ``process_cwd`` ≠ racine du dépôt, ``uvicorn`` a été lancé depuis un autre dossier.
        "process_cwd": os.getcwd(),
        "main_py_path": str(Path(__file__).resolve()),
        "amort_engine_id": PRICER_AMORT_ENGINE_ID,
        "amort_engine": {
            "module_path": str(am_path) if am_path else None,
            "mtime": am_mtime,
        },
    }
    return JSONResponse(
        content=payload,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            # Si cet en-tête est absent dans F12 → Réseau, ce n’est pas cette API (autre process sur :8001).
            "X-Pricer-Health-Version": "2",
        },
    )


@app.get("/api/pricer-meta")
def pricer_meta() -> PlainTextResponse:
    """
    Texte brut (pas de cache navigateur typique) : si cette URL ne répond pas ou ne contient pas
    ``excel-amm-``, ce n’est pas cette API.
    """
    body = (
        f"main_py={Path(__file__).resolve()}\n"
        f"project_root={_ROOT.resolve()}\n"
        f"cwd={os.getcwd()}\n"
        f"amort_engine_id={PRICER_AMORT_ENGINE_ID}\n"
        "prix_mr_source=table_titre_valo_v3\n"
    )
    return PlainTextResponse(
        body,
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.post("/api/curve")
def build_curve(req: CurveRequest):
    try:
        curve = _make_curve(req)
        j_grid = float(curve.joint)
        grid = maturity_grid(req.max_days, req.step_short, req.step_long, j_grid)
        ordered, blocs = excel_style_maturity_order(grid, j_grid, req.step_short)
        table = curve.build_table(ordered)
        table = table.assign(_bloc=blocs)
        out = _df_to_records(table)
        anchor = (req.zc_schedule_anchor_date or "").strip()[:10]
        if not anchor:
            raise HTTPException(
                status_code=400,
                detail="curve.zc_schedule_anchor_date (AAAA-MM-JJ) est requis pour construire l'échéancier ZC annuel.",
            )
        schedule = _schedule_table_records(curve, root=_ROOT, date_courbe=anchor)
        # Courbe ZC = colonne TauxZC de l’échéancier (maturités fixes, bootstrap Excel).
        md_s = [float(r["Maturity_days"]) for r in schedule]
        zc_s = [float(r["Taux_ZC_pct_full"]) for r in schedule]
        taux_s = [float(r["Taux_pct"]) for r in schedule]
        zc_clean = np.nan_to_num(np.asarray(zc_s, dtype=float), nan=0.0, posinf=0.0, neginf=0.0).tolist()
        chart = {
            "maturity_days": md_s,
            "zc_pct": zc_clean,
            "actuarial_pct": list(zc_clean),
            "quoted_pct": taux_s,
        }
        return {"table": out, "chart": chart, "schedule_table": schedule}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/curve/pillars-from-histo")
def curve_pillars_from_histo(req: CurvePillarsFromHistoRequest):
    root = Path(__file__).resolve().parent.parent
    try:
        return _extraire_piliers_depuis_histo(root, req.date_courbe, req.courbe)
    except HTTPException:
        raise
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erreur extraction HISTO_COURBE_TAUX: {e}") from e


def _verrou_prix_marche_depuis_grilles_amort(
    rows_ui: list[dict[str, Any]],
    amortissement_tables: list[dict[str, Any]],
) -> None:
    """
    Dernière passe avant la réponse JSON : réaligne « Prix arrondi » / clean / dirty sur
    ``prix_somme_flux_actualises`` lorsque l’échéancier pilote le prix (même logique que la grille).

    Couvre les cas où un worker ancien ou une étape intermédiaire laissait un prix proche de
    Σ(Flux actualisé arrondis colonne) au lieu du NPV pleine précision attendu par Manar.
    """
    if not rows_ui or not amortissement_tables:
        return
    by_tab: dict[str, dict[str, Any]] = {}
    for t in amortissement_tables:
        if not isinstance(t, dict):
            continue
        c_am = _obl_amort_mod._normaliser_code(t.get("code"))
        if c_am:
            by_tab[str(c_am)] = t
        c_sm = _normaliser_code_simple(t.get("code"))
        if c_sm:
            by_tab.setdefault(c_sm, t)
    for row in rows_ui:
        c_am = _obl_amort_mod._normaliser_code(row.get("CODE"))
        c_sm = _normaliser_code_simple(row.get("CODE"))
        tab = by_tab.get(str(c_am)) if c_am else None
        if tab is None and c_sm:
            tab = by_tab.get(c_sm)
        if not tab:
            continue
        pilot = tab.get("prix_clean_pilote_par_echeancier")
        if pilot is None:
            pilot = _obl_amort_mod._table_amort_doit_aligner_prix(tab)
        else:
            pilot = bool(pilot)
        if not pilot:
            continue
        raw_sum = tab.get("prix_somme_flux_actualises")
        if raw_sum is None:
            continue
        sum_clean = float(raw_sum)
        if not math.isfinite(sum_clean) or sum_clean <= 0.0:
            continue
        cc = _safe_float(row.get("Coupon couru"), 0.0)
        row["Prix arrondi"] = round(sum_clean, 6)
        row["Prix clean"] = round(sum_clean, 6)
        row["Prix dirty"] = round(sum_clean + cc, 6)


@app.post("/api/marche/valorize")
def marche_valorize(req: MarcheValorizeRequest | None = None):
    """
    Valorisation batch pour l’UI « Pricing obligataire » : lecture du fichier base titre oblig (voir ``data/obligations/``),
    actualisation (taux secondaire interpolé + spread), métriques de risque ; lignes au format attendu par le front.
    """
    root = Path(__file__).resolve().parent.parent
    req = req or MarcheValorizeRequest()
    zc_path = root / (req.courbe_zc_py or "pricing/curves/courbe_zc.py")
    try:
        courbe = charger_courbe_zc_depuis_fichier(zc_path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Courbe ZC: {e}") from e
    try:
        xlsx = resoudre_fichier_base_titre_oblig(root, req.excel_xlsx)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    if os.environ.get("PRICER_DEBUG_PRIX_MR", "").strip() in ("1", "true", "TRUE", "oui", "OUI"):
        _debug_prix_mr_stdout(root, xlsx)
    bam_cc: dict[float, float] | None = None
    bam_cl: dict[float, float] | None = None
    # Courbe MAR_JJ + échéancier ZC : **priorité à ``valuation_date``** (histo SQL) dès qu’elle est
    # renseignée. Sinon un ``req.curve`` chargé pour une autre date (ex. 26/03) restait utilisé pour
    # ``YieldCurve`` / Formule B alors que le schedule ZC pouvait s’ancrer sur le 06/03 → CT 2,27 %
    # au lieu de 2,25 % (9487).
    curve_req_for_amort: CurveRequest | None = None
    try:
        filt_brut = (req.code_maroclear or "").strip()
        filt_norm, astuce_filtre = _normaliser_filtre_maroclear_typo(filt_brut)
        charger_tous_les_titres = bool(req.prix_manarr_pricer_tous) or bool(req.feuil1_pricer_tous) or not filt_norm
        df_in = _charger_base_titre_oblg_cache(
            xlsx,
            codes_filter=None if charger_tous_les_titres else [filt_norm],
        )

        # Si l’UI impose encore ``filt_brut`` sur ``df_work`` alors que l’on valorise **toutes**
        # les lignes Prix Manar (ou Feuil1), ``rows_ui`` ne contient qu’un CODE : les autres
        # titres Manar ne matchent pas ``prix_reference`` / ``_enrichir_*`` → « recalcul complet »
        # et écarts fantômes (ex. 9360 alors que le filtre affiche 100974).
        elargir_df_work_au_referentiel_complet = bool(req.prix_manarr_pricer_tous) or bool(
            req.feuil1_pricer_tous
        )
        code_pour_df_work = "" if elargir_df_work_au_referentiel_complet else filt_brut
        df_work, col_code_fichier = filtrer_dataframe_par_code_maroclear(df_in, code_pour_df_work)
        if filt_brut and col_code_fichier is None:
            raise HTTPException(
                status_code=400,
                detail="Colonne « CODE » introuvable dans dbo.referentiel_titre : impossible de filtrer par Maroclear.",
            )
        if filt_brut and not elargir_df_work_au_referentiel_complet and df_work.empty:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Le code Maroclear « {filt_brut} » n'existe pas dans la base de données "
                    "dbo.referentiel_titre."
                ),
            )
        if filt_brut and elargir_df_work_au_referentiel_complet and col_code_fichier:
            mask_filt = df_in[col_code_fichier].apply(
                lambda v, fb=filt_brut: ligne_code_maroclear_correspond(v, fb)
            )
            if not bool(mask_filt.any()):
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"Le code Maroclear « {filt_brut} » n'existe pas dans la base de données "
                        "dbo.referentiel_titre."
                    ),
                )

        v_iso = str(req.valuation_date or "").strip()[:10]
        if v_iso:
            try:
                _pil_mjj = _extraire_piliers_depuis_histo(root, v_iso, "MAR_JJ")
                curve_req_for_amort = CurveRequest(
                    short=[PillarShort(**p) for p in _pil_mjj["short"]],
                    long=[PillarLong(**p) for p in _pil_mjj["long"]],
                    joint_days=float(_pil_mjj.get("joint_days", 325.0)),
                    max_days=11000,
                    step_short=50,
                    step_long=100,
                    zc_schedule_anchor_date=v_iso,
                )
                bam_cc, bam_cl = _courbes_bam_depuis_requete(curve_req_for_amort)
            except HTTPException:
                # Ne pas masquer 404 date / 503 SQL : sinon repli silencieux sur req.curve → mauvaise courbe (ex. 9487).
                raise
            except Exception as exc:
                logger.warning(
                    "MAR_JJ indisponible pour valuation_date=%s (repli req.curve si présent) : %s",
                    v_iso,
                    exc,
                )
                curve_req_for_amort = None
                bam_cc, bam_cl = None, None
        if curve_req_for_amort is None and req.curve is not None and req.curve.short and req.curve.long:
            curve_req_for_amort = req.curve
            bam_cc, bam_cl = _courbes_bam_depuis_requete(req.curve)

        df_out, det_cols = valoriser_dataframe_base_titre(
            df_work,
            courbe,
            valuation_date=req.valuation_date,
            bam_courbe_court=bam_cc,
            bam_courbe_long=bam_cl,
            progress_label="Valorisation obligations",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Base titre: {e}") from e

    raw = _df_to_records(df_out)
    rows_ui = [_row_to_marche_ui(r, req.valuation_date) for r in raw]
    nb_val = len(raw)

    amortissement_tables: list[dict] = []
    amortissement_error: str | None = None
    feuilles_amort_diag = _diagnostic_feuilles_amortissement_cache(xlsx)
    fn_taux_zc_schedule: Callable[[float], float] | None = None
    fn_taux_zc_schedule_a: Callable[[float], float] | None = None
    if curve_req_for_amort is not None:
        try:
            curve_tracee = _make_curve(curve_req_for_amort)
            _zc_anchor = (req.valuation_date or "").strip()[:10] or (
                (req.curve.zc_schedule_anchor_date or "").strip()[:10] if req.curve else ""
            )
            if not _zc_anchor:
                raise HTTPException(
                    status_code=400,
                    detail="valuation_date ou curve.zc_schedule_anchor_date requis pour l'échéancier ZC annuel.",
                )
            _sched_zc = _schedule_table_records(
                curve_tracee, root=root, date_courbe=_zc_anchor, courbe="MAR_JJ"
            )
            fn_taux_zc_schedule = lambda j, rows=_sched_zc: _interp_taux_zc_actuariel_depuis_schedule_jours(j, rows)
            fn_taux_zc_schedule_a = lambda a, rows=_sched_zc: _interp_taux_zc_depuis_schedule_annuel(a, rows)
        except HTTPException:
            raise
        except Exception:
            fn_taux_zc_schedule = None
            fn_taux_zc_schedule_a = None
    try:
        if bam_cc is not None and bam_cl is not None and len(bam_cc) >= 1 and len(bam_cl) >= 1:

            def _ts_amort(j: float) -> float:
                # Pas d'ARRONDI à 6 déc. ici : il peut rabattre 0,0263199… en 0,026319 puis la troncature
                # FIX/AA (5 dec.) affiche 2,631 % au lieu de 2,632 % (Manar). La précision est bornée ensuite
                # par ``_decimal_taux_courbe_fix_aa_pour_actu`` (round 12 + troncature 5 dec.).
                return float(
                    taux_secondaire_interpole_formule_b(
                        float(j),
                        bam_cc,
                        bam_cl,
                        ndigits=None,
                    )
                )
        else:

            def _ts_amort(j: float) -> float:
                return float(interp_taux_secondaire_jours(float(j), courbe))

        amortissement_tables = construire_tables_amortissement_pour_valorisation(
            xlsx,
            raw,
            rows_ui,
            valuation_date=req.valuation_date,
            taux_secondaire_a_j=_ts_amort,
            taux_zc_schedule_j=fn_taux_zc_schedule,
            taux_zc_schedule_a=fn_taux_zc_schedule_a,
            df_work=df_work,
            col_code_fichier=col_code_fichier,
            det_cols=det_cols,
            codes_filter=[_normaliser_code_simple(r.get("CODE")) for r in rows_ui],
        )
        appliquer_grille_amort_sur_lignes_marche(rows_ui, amortissement_tables)
    except Exception as e:
        amortissement_tables = []
        amortissement_error = f"{type(e).__name__}: {e}"
    # Même filtre / clés que ``appliquer_grille_amort_sur_lignes_marche`` (``_normaliser_code`` sur le code table).
    # Sinon une ligne peut être absente du skip ATP alors qu’elle a reçu la grille → « Prix arrondi » repasse au clean.
    codes_grille_amort: set[str] = set()
    for t in amortissement_tables:
        try:
            pa = t.get("prix_actualise")
            if (
                pa is not None
                and math.isfinite(float(pa))
                and float(pa) > 0
                and bool(t.get("appliquer_prix_echeancier"))
            ):
                c_am = _obl_amort_mod._normaliser_code(t.get("code"))
                if c_am:
                    codes_grille_amort.add(c_am)
                c_sm = _normaliser_code_simple(t.get("code"))
                if c_sm:
                    codes_grille_amort.add(c_sm)
        except (TypeError, ValueError):
            pass
    _reappliquer_brut_atp_sur_lignes_ui(rows_ui, raw, codes_grille_amort)
    msg = None
    if not rows_ui:
        if filt_brut and len(df_work) > 0 and nb_val == 0:
            msg = (
                f"Ligne(s) trouvée(s) pour le CODE « {filt_brut} » dans la colonne « {col_code_fichier} », "
                f"mais aucune valorisation : échéance déjà passée par rapport à la date de valorisation, "
                f"ou colonnes obligatoires manquantes (NOMINAL, VALEUR_TAUX, DATE_ECHEANCE). "
                f"Changez la date ou ouvrez une obligation encore active."
            )
        elif nb_val == 0:
            n_echeues = int(det_cols.get("nb_lignes_echeance_depassee") or 0)
            msg = (
                f"Aucune ligne exploitable dans {xlsx.name}. Indispensable : nominal (ou VN), taux coupon, "
                f"et maturité (jours/années) **ou** une date d’échéance **postérieure** à la date de valorisation. "
                f"Colonnes détectées : nominal={det_cols.get('col_nominal')!s}, taux={det_cols.get('col_taux_coupon')!s}, "
                f"spread={det_cols.get('col_spread')!s}, date échéance={det_cols.get('col_date_echeance')!s}, "
                f"période coupon={det_cols.get('cper')!s}. "
                f"Fichier conseillé : {CHEMIN_BASE_TITRE_OBLIG_PREFERENTIEL.as_posix()}."
            )
            if n_echeues > 0:
                msg += (
                    f" — {n_echeues} ligne(s) ignorée(s) : échéance déjà passée par rapport à la date de valorisation "
                    f"(ex. obligations remboursées ; filtrez les titres actifs ou changez la date)."
                )
    echantillon_atp: dict | None = None
    if raw:
        r0 = raw[0]
        if str(r0.get("moteur_prix") or "") == "ATP":
            echantillon_atp = {
                "taux_rendement_atp_utilise": r0.get("taux_rendement_atp_utilise"),
                "actuariel_base": r0.get("actuariel_base"),
                "prix_clean_atp": r0.get("prix_clean_atp"),
            }

    feuil1_titres_brut, feuil1_sheet = _lire_feuil1_liste_titres(xlsx)
    prix_manarr_rows, prix_manarr_sheet = _lire_prix_manarr_table(root, req.valuation_date)
    if filt_norm and not bool(req.prix_manarr_pricer_tous):
        prix_manarr_rows = [
            r for r in prix_manarr_rows if ligne_code_maroclear_correspond(r.get("titre"), filt_norm)
        ]
    prix_mr_map = _charger_prix_mr_cache_marche(root, xlsx)
    _appliquer_prix_mr_depuis_table_titre_valo(rows_ui, feuil1_titres_brut)

    for r in rows_ui:
        r.pop("_marche_ligne_amortissable", None)

    feuil1_titres: list[dict[str, Any]] = list(feuil1_titres_brut)
    if (
        bool(req.feuil1_pricer_tous)
        and col_code_fichier
        and len(feuil1_titres_brut) > 0
    ):
        codes_o = [str(t.get("titre") or "").strip() for t in feuil1_titres_brut]
        df_fe = _df_subset_codes_ordre(df_in, str(col_code_fichier), codes_o)
        if not df_fe.empty:
            rows_fe = _valoriser_slice_feuil1_batch(
                df_fe,
                courbe=courbe,
                req=req,
                bam_cc=bam_cc,
                bam_cl=bam_cl,
                xlsx=xlsx,
                prix_mr_map=prix_mr_map,
                fn_taux_zc_schedule=fn_taux_zc_schedule,
                fn_taux_zc_schedule_a=fn_taux_zc_schedule_a,
                col_code_fichier=str(col_code_fichier),
            )
            by_line = {_normaliser_code_simple(r.get("CODE")): r for r in rows_fe}
            merged: list[dict[str, Any]] = []
            for t in feuil1_titres_brut:
                cn = _normaliser_code_simple(t.get("titre"))
                u = by_line.get(cn) if cn else None
                # Prix arrondi = toujours le moteur (colonne UI « Prix arrondi »).
                pa_f = _safe_float(u.get("Prix arrondi"), float("nan")) if u else float("nan")
                valo_out: float | None = float(pa_f) if u is not None and math.isfinite(pa_f) else None
                # valo(Prix MR) : d’abord la feuille Excel (PRICE puis colonne valo), sinon Prix MR moteur.
                ex_price = _safe_float(t.get("prix_mr"), float("nan"))
                ex_valo_col = _safe_float(t.get("valo"), float("nan"))
                pm_engine = _safe_float(u.get("Prix MR"), float("nan")) if u else float("nan")
                prix_mr_out: float | None = None
                if math.isfinite(ex_price):
                    prix_mr_out = float(ex_price)
                elif math.isfinite(ex_valo_col):
                    prix_mr_out = float(ex_valo_col)
                elif math.isfinite(pm_engine):
                    prix_mr_out = float(pm_engine)
                ecart_out: float | None = None
                if valo_out is not None and prix_mr_out is not None:
                    ecart_cents = int(round(valo_out * 100.0)) - int(round(float(prix_mr_out) * 100.0))
                    ecart_out = float(ecart_cents) / 100.0
                merged.append(
                    {
                        "titre": str(t.get("titre") or "").strip(),
                        "date": t.get("date"),
                        "valo": valo_out,
                        "prix_mr": prix_mr_out,
                        "ecart": ecart_out,
                    }
                )
            feuil1_titres = merged

    # Verrou final: sur la vue marché, Prix MR provient strictement de la colonne
    # ``valo`` du tableau titre/valo (match ``titre == CODE``), sans fallback moteur.
    map_titre_valo_final: dict[str, float] = {}
    for t in feuil1_titres_brut:
        code_t = _normaliser_code_simple(t.get("titre"))
        if not code_t:
            continue
        valo_t = _safe_float(t.get("valo"), float("nan"))
        if math.isfinite(valo_t):
            map_titre_valo_final[code_t] = float(valo_t)

    for r in rows_ui:
        code_n = _normaliser_code_simple(r.get("CODE"))
        pmr = map_titre_valo_final.get(code_n) if code_n else None
        if pmr is None:
            r["Prix MR"] = None
            r["Ecart Prix arrondi - Prix MR"] = None
            continue
        r["Prix MR"] = round(float(pmr), 2)

    _verrou_prix_marche_depuis_grilles_amort(rows_ui, amortissement_tables)

    for r in rows_ui:
        code_n = _normaliser_code_simple(r.get("CODE"))
        pmr = map_titre_valo_final.get(code_n) if code_n else None
        if pmr is None:
            continue
        pmr_2 = round(float(pmr), 2)
        pa_2 = round(_safe_float(r.get("Prix arrondi"), 0.0), 2)
        ecart_cents = int(round(pa_2 * 100.0)) - int(round(pmr_2 * 100.0))
        r["Ecart Prix arrondi - Prix MR"] = float(ecart_cents) / 100.0

    # Après verrou grille : même « Prix arrondi » que Valorisation (évite écart 0 en filtre puis
    # « recalcul complet » ≠ fichier lorsque ``prix_reference`` était pris avant verrou).
    if bool(req.prix_manarr_pricer_tous):
        prix_manarr_rows = _valoriser_prix_manarr_rows(
            prix_manarr_rows,
            df_in,
            rows_ui_reference=rows_ui,
            col_code_fichier=col_code_fichier,
            courbe=courbe,
            req=req,
            bam_cc=bam_cc,
            bam_cl=bam_cl,
            xlsx=xlsx,
            prix_mr_map=prix_mr_map,
            fn_taux_zc_schedule=fn_taux_zc_schedule,
            fn_taux_zc_schedule_a=fn_taux_zc_schedule_a,
        )
    _enrichir_prix_manarr_depuis_rows_ui(prix_manarr_rows, rows_ui)

    diagnostic = {
        "colonnes_feuille": [str(c) for c in df_in.columns],
        "colonnes_utilisees": {k: (str(v) if v else None) for k, v in det_cols.items() if k.startswith("col_")},
        "echantillon_atp": echantillon_atp,
        "colonne_code_fichier": col_code_fichier,
        "nb_lignes_fichier_total": len(df_in),
        "nb_lignes_apres_filtre_code": len(df_work),
        "nb_lignes_lues": int(det_cols.get("nb_lignes_lues", 0)),
        "nb_lignes_valorisees": nb_val,
        "nb_lignes_affichees": len(rows_ui),
        "nb_lignes_echeance_depassee": int(det_cols.get("nb_lignes_echeance_depassee") or 0),
        "filtre_code": filt_brut or None,
        "filtre_code_normalise": filt_norm if filt_norm and filt_norm != filt_brut else None,
        "astuce_filtre": astuce_filtre,
        "taux_secondaire_source": det_cols.get("taux_secondaire_source"),
        "amortissement_tables_count": len(amortissement_tables),
        "amortissement_error": amortissement_error,
        "feuilles_classeur": feuilles_amort_diag.get("sheet_names"),
        "feuille_echeancier_detectee": feuilles_amort_diag.get("feuille_echeancier"),
        "feuille_referentiel_detectee": feuilles_amort_diag.get("feuille_referentiel"),
        "nb_prix_mr_charges": len(prix_mr_map),
        "feuil1_feuille": feuil1_sheet,
        "nb_feuil1_titres": len(feuil1_titres),
        "prix_manarr_feuille": prix_manarr_sheet,
        "nb_prix_manarr_lignes": len(prix_manarr_rows),
        "prix_manarr_pricer_tous": bool(req.prix_manarr_pricer_tous),
        # Permet de vérifier que le front parle au bon worker (flux actualisé = logique de ce module).
        "amort_engine_id": PRICER_AMORT_ENGINE_ID,
        "amort_schedule_module": str(Path(_obl_amort_mod.__file__).resolve()),
    }
    body = {
        "rows": rows_ui,
        "nb_lignes": len(rows_ui),
        "fichier": str(xlsx.name),
        "message": msg,
        "diagnostic": diagnostic,
        "amortissement_tables": amortissement_tables,
        "feuil1_titres": feuil1_titres,
        "prix_manarr": prix_manarr_rows,
    }
    return JSONResponse(
        content=body,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "X-Pricer-Amort-Engine-ID": PRICER_AMORT_ENGINE_ID,
        },
    )


_MBI_TRANCHES = frozenset({"global", "monetaire", "ct", "mt", "mlt", "lt"})


def _normalize_mbi_tranche(raw: str | None) -> str:
    if raw is None or not str(raw).strip():
        return "global"
    s = str(raw).strip().lower().replace(" ", "_")
    aliases: dict[str, str] = {
        "mbi_global": "global",
        "toutes": "global",
        "all": "global",
        "mbi_monétaire": "monetaire",
        "mbi_monetaire": "monetaire",
        "monetary": "monetaire",
        "mbi_ct": "ct",
        "court_terme": "ct",
        "mbi_mt": "mt",
        "moyen_terme": "mt",
        "mbi_mlt": "mlt",
        "mbi_lt": "lt",
        "long_terme": "lt",
    }
    return aliases.get(s, s)


def _mbi_tranche_label(tranche: str) -> str:
    return {
        "global": "MBI Global",
        "monetaire": "MBI Monétaire",
        "ct": "MBI CT",
        "mt": "MBI MT",
        "mlt": "MBI MLT",
        "lt": "MBI LT",
    }.get(tranche, tranche)


def _portfolio_maturite_residuelle_annees_sql() -> str:
    return (
        "(CAST(DATEDIFF(day, TRY_CAST(? AS date), TRY_CAST(date_echeance AS date)) AS float) / 365.25)"
    )


def _portfolio_eligible_referentiel(date_valo: str, limit: int | None, mbi_tranche: str = "global") -> pd.DataFrame:
    top_clause = ""
    if limit is not None and int(limit) > 0:
        top_clause = f"TOP ({int(limit)}) "
    mat_y = _portfolio_maturite_residuelle_annees_sql()
    t = _normalize_mbi_tranche(mbi_tranche)
    if t == "global":
        mat_filter = ""
        params_list: list[Any] = [date_valo]
    elif t == "monetaire":
        mat_filter = f" AND {mat_y} < 1"
        params_list = [date_valo, date_valo]
    elif t == "ct":
        mat_filter = f" AND {mat_y} >= 1 AND {mat_y} < 3"
        params_list = [date_valo, date_valo, date_valo]
    elif t == "mt":
        mat_filter = f" AND {mat_y} >= 3 AND {mat_y} < 5"
        params_list = [date_valo, date_valo, date_valo]
    elif t == "mlt":
        mat_filter = f" AND {mat_y} >= 5 AND {mat_y} < 10"
        params_list = [date_valo, date_valo, date_valo]
    elif t == "lt":
        mat_filter = f" AND {mat_y} >= 10"
        params_list = [date_valo, date_valo]
    else:
        raise ValueError(f"Tranche MBI inconnue: {mbi_tranche}")

    return read_sql_dataframe(
        f"""
        SELECT {top_clause}*
        FROM dbo.referentiel_titre
        WHERE code IS NOT NULL
          AND LTRIM(RTRIM(code)) <> ''
          AND code NOT LIKE '%[^0-9]%'
          AND date_echeance > ?
          AND TRY_CONVERT(decimal(38, 10), nominal) IS NOT NULL
          AND TRY_CONVERT(decimal(38, 10), nominal) <> 0
          AND (flag_actif IS NULL OR UPPER(LTRIM(RTRIM(flag_actif))) IN ('O', '1', 'Y', 'YES', 'TRUE', 'A', 'ACTIF'))
          {mat_filter}
        ORDER BY TRY_CONVERT(bigint, code), code
        """,
        tuple(params_list),
    )


def _portfolio_safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def _portfolio_first_text(row: pd.Series, *names: str) -> str:
    for name in names:
        for c in row.index:
            if str(c).strip().upper() == name.strip().upper():
                v = row.get(c)
                if v is not None and str(v).strip() and str(v).strip().lower() not in ("nan", "none", "nat"):
                    return str(v).strip()
    return ""


@lru_cache(maxsize=128)
def _portfolio_snapshot_cached(
    date_valo: str,
    mbi_tranche: str = "global",
    limit: int | None = None,
) -> dict[str, Any]:
    return _portfolio_snapshot_payload(date_valo, mbi_tranche, limit)


def _portfolio_snapshot_payload(
    date_valo: str,
    mbi_tranche: str = "global",
    limit: int | None = None,
) -> dict[str, Any]:
    """
    Portefeuille obligataire type indice MBI : titres actifs depuis ``dbo.referentiel_titre``,
    filtrés par maturité résiduelle (années = jours / 365,25), valorisés un par un.
    Une erreur sur un titre n'arrête pas le portefeuille.
    """
    try:
        d_valo = datetime.fromisoformat(str(date_valo)[:10]).date()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"date_valo invalide: {date_valo}") from e
    if limit is not None and int(limit) <= 0:
        limit = None

    tranche_key = _normalize_mbi_tranche(mbi_tranche)
    if tranche_key not in _MBI_TRANCHES:
        raise HTTPException(
            status_code=400,
            detail=f"mbi_tranche invalide: {mbi_tranche!r} (attendu: global, monetaire, ct, mt, mlt, lt)",
        )

    root = Path(__file__).resolve().parent.parent
    try:
        pillars = _extraire_piliers_depuis_histo(root, d_valo.isoformat(), "MAR_JJ")
        curve_req = CurveRequest(
            short=[PillarShort(**p) for p in pillars["short"]],
            long=[PillarLong(**p) for p in pillars["long"]],
            joint_days=float(pillars.get("joint_days", 325.0)),
            max_days=11000,
            step_short=50,
            step_long=100,
            zc_schedule_anchor_date=d_valo.isoformat(),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Courbe BAM: {e}") from e

    bam_cc, bam_cl = _courbes_bam_depuis_requete(curve_req)

    zc_path = root / "pricing/curves/courbe_zc.py"
    try:
        courbe = charger_courbe_zc_depuis_fichier(zc_path)
        xlsx = resoudre_fichier_base_titre_oblig(root, None)
        df_candidates = _portfolio_eligible_referentiel(d_valo.isoformat(), limit, tranche_key)
    except SqlDataAccessError as e:
        raise HTTPException(status_code=503, detail=f"SQL Server indisponible: {e}") from e
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Initialisation portefeuille: {e}") from e

    try:
        curve_tracee = _make_curve(curve_req)
        schedule_zc = _schedule_table_records(
            curve_tracee, root=root, date_courbe=d_valo.isoformat(), courbe="MAR_JJ"
        )
        fn_taux_zc_schedule = lambda j, rows=schedule_zc: _interp_taux_zc_actuariel_depuis_schedule_jours(j, rows)
        fn_taux_zc_schedule_a = lambda a, rows=schedule_zc: _interp_taux_zc_depuis_schedule_annuel(a, rows)
    except HTTPException:
        raise
    except Exception:
        fn_taux_zc_schedule = None
        fn_taux_zc_schedule_a = None

    def _ts_amort(j: float) -> float:
        return float(
            taux_secondaire_interpole_formule_b(
                float(j),
                bam_cc,
                bam_cl,
                ndigits=NDIGITS_TAUX_SECONDAIRE_FORMULE_B_DEFAUT,
            )
        )

    positions: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    col_code_fichier = "CODE"

    for _, src_row in df_candidates.iterrows():
        code = _portfolio_first_text(src_row, "CODE")
        if not code:
            continue
        try:
            df_one = pd.DataFrame([src_row.to_dict()])
            df_out, det_cols = valoriser_dataframe_base_titre(
                df_one,
                courbe,
                valuation_date=d_valo.isoformat(),
                bam_courbe_court=bam_cc,
                bam_courbe_long=bam_cl,
            )
            raw = _df_to_records(df_out)
            if not raw:
                raise ValueError("Titre non valorisé par le moteur existant.")
            rows_ui = [_row_to_marche_ui(r, d_valo.isoformat()) for r in raw]
            amort_tables = construire_tables_amortissement_pour_valorisation(
                xlsx,
                raw,
                rows_ui,
                valuation_date=d_valo.isoformat(),
                taux_secondaire_a_j=_ts_amort,
                taux_zc_schedule_j=fn_taux_zc_schedule,
                taux_zc_schedule_a=fn_taux_zc_schedule_a,
                df_work=df_one,
                col_code_fichier=col_code_fichier,
                det_cols=det_cols,
                codes_filter=[_normaliser_code_simple(code)],
            )
            appliquer_grille_amort_sur_lignes_marche(rows_ui, amort_tables)
            codes_grille: set[str] = set()
            for tab in amort_tables:
                try:
                    pa = tab.get("prix_actualise")
                    if (
                        pa is not None
                        and math.isfinite(float(pa))
                        and float(pa) > 0
                        and bool(tab.get("appliquer_prix_echeancier"))
                    ):
                        c_am = _obl_amort_mod._normaliser_code(tab.get("code"))
                        if c_am:
                            codes_grille.add(c_am)
                except (TypeError, ValueError):
                    pass
            _reappliquer_brut_atp_sur_lignes_ui(rows_ui, raw, codes_grille)
            if not rows_ui:
                raise ValueError("Aucune ligne UI produite par le moteur.")
            ui = rows_ui[0]
            quantite = _portfolio_safe_float(src_row.get("NOMBRE_TITRE_EMIS"), 1.0)
            if quantite <= 0:
                quantite = 1.0
            price = _portfolio_safe_float(ui.get("Prix dirty"), 0.0)
            if price <= 0:
                price = _portfolio_safe_float(ui.get("Prix arrondi"), 0.0)
            if price <= 0:
                price = _portfolio_safe_float(ui.get("Prix clean"), 0.0)
            if price <= 0:
                raise ValueError("Prix nul ou indisponible.")
            nominal = _portfolio_safe_float(ui.get("Nominal"), _portfolio_safe_float(src_row.get("NOMINAL"), 0.0))
            coupon_couru = _portfolio_safe_float(ui.get("Coupon couru"), 0.0)
            sensibilite = _portfolio_safe_float(ui.get("Sensibilité"), 0.0)
            market_value = price * quantite
            dv01 = market_value * sensibilite * 0.0001
            positions.append(
                {
                    "code": str(code),
                    "description": str(ui.get("Description") or ui.get("description") or _portfolio_first_text(src_row, "DESCRIPTION", "LIB_COURT")),
                    "emetteur": _portfolio_first_text(src_row, "EMETTEUR"),
                    "secteur": _portfolio_first_text(src_row, "SECTEUR_ECONOMIQUE", "SECTEUR"),
                    "quantite": quantite,
                    "nominal": nominal,
                    "price": price,
                    "market_value": market_value,
                    "weight": 0.0,
                    "ytm": _portfolio_safe_float(ui.get("Rendement (YTM)"), 0.0),
                    "duration": _portfolio_safe_float(ui.get("Duration titre"), 0.0),
                    "sensibilite": sensibilite,
                    "convexite": _portfolio_safe_float(ui.get("Convexité"), 0.0),
                    "spread": _portfolio_safe_float(ui.get("Spread"), 0.0),
                    "maturite_residuelle": _portfolio_safe_float(ui.get("Maturité résiduelle (jours)"), 0.0),
                    "coupon_couru": coupon_couru,
                    "dv01": dv01,
                    "date_echeance": str(ui.get("Date d'échéance") or _portfolio_first_text(src_row, "DATE_ECHEANCE")),
                }
            )
        except Exception as e:
            errors.append({"code": str(code), "reason": str(e)})

    total_market_value = sum(float(p["market_value"]) for p in positions)
    if total_market_value > 0:
        for p in positions:
            p["weight"] = float(p["market_value"]) / total_market_value

    def _weighted(key: str) -> float:
        return sum(float(p.get("weight") or 0.0) * float(p.get(key) or 0.0) for p in positions)

    portfolio_dv01 = sum(float(p.get("dv01") or 0.0) for p in positions)
    issuer_weights: dict[str, float] = {}
    sector_weights: dict[str, float] = {}
    maturity_weights: dict[str, float] = {}
    for p in positions:
        issuer = str(p.get("emetteur") or "").strip() or "Non renseigné"
        issuer_weights[issuer] = issuer_weights.get(issuer, 0.0) + float(p.get("weight") or 0.0)
        sector = str(p.get("secteur") or "").strip() or "Non renseigné"
        sector_weights[sector] = sector_weights.get(sector, 0.0) + float(p.get("weight") or 0.0)
        mat_y = float(p.get("maturite_residuelle") or 0.0) / 365.25
        if mat_y < 1.0:
            mat_bucket = "< 1 an"
        elif mat_y < 3.0:
            mat_bucket = "1-3 ans"
        elif mat_y < 5.0:
            mat_bucket = "3-5 ans"
        elif mat_y < 10.0:
            mat_bucket = "5-10 ans"
        else:
            mat_bucket = "> 10 ans"
        maturity_weights[mat_bucket] = maturity_weights.get(mat_bucket, 0.0) + float(p.get("weight") or 0.0)
    top_issuer = ""
    top_issuer_weight = 0.0
    if issuer_weights:
        top_issuer, top_issuer_weight = max(issuer_weights.items(), key=lambda kv: kv[1])

    summary = {
        "total_market_value": total_market_value,
        "number_of_bonds": len(positions),
        "weighted_ytm": _weighted("ytm"),
        "weighted_duration": _weighted("duration"),
        "weighted_sensibilite": _weighted("sensibilite"),
        "weighted_convexite": _weighted("convexite"),
        "weighted_spread": _weighted("spread"),
        "total_accrued_coupon": sum(float(p.get("coupon_couru") or 0.0) * float(p.get("quantite") or 0.0) for p in positions),
        "portfolio_dv01": portfolio_dv01,
        "max_position_weight": max((float(p.get("weight") or 0.0) for p in positions), default=0.0),
        "top_issuer": top_issuer,
        "top_issuer_weight": top_issuer_weight,
        "number_of_issuers": len(issuer_weights),
    }
    risk_contrib = []
    for p in sorted(positions, key=lambda x: float(x.get("dv01") or 0.0), reverse=True):
        dv01_i = float(p.get("dv01") or 0.0)
        risk_contrib.append(
            {
                "code": p.get("code"),
                "description": p.get("description"),
                "emetteur": p.get("emetteur"),
                "weight": p.get("weight"),
                "dv01": dv01_i,
                "contribution_dv01_pct": dv01_i / portfolio_dv01 if portfolio_dv01 > 0 else 0.0,
            }
        )
    allocations = {
        "by_issuer": [
            {"name": k, "weight": v}
            for k, v in sorted(issuer_weights.items(), key=lambda kv: kv[1], reverse=True)
        ],
        "by_sector": [
            {"name": k, "weight": v}
            for k, v in sorted(sector_weights.items(), key=lambda kv: kv[1], reverse=True)
        ],
        "by_maturity": [
            {"name": k, "weight": maturity_weights.get(k, 0.0)}
            for k in ("< 1 an", "1-3 ans", "3-5 ans", "5-10 ans", "> 10 ans")
            if maturity_weights.get(k, 0.0) > 0.0
        ],
        "risk_contribution": risk_contrib,
    }

    return {
        "mode": "snapshot",
        "portfolio_name": _mbi_tranche_label(tranche_key),
        "mbi_tranche": tranche_key,
        "date_valo": d_valo.isoformat(),
        "summary": summary,
        "allocations": allocations,
        "positions": positions,
        "errors": errors,
    }


@app.get("/api/portfolio/valuation")
def portfolio_valuation(
    date_valo: str | None = None,
    mbi_tranche: str = "global",
    valuation_date: str | None = Query(default=None),
    index_type: str | None = Query(default=None),
    limit: int | None = None,
):
    """
    Mode 1 — snapshot : photographie du benchmark Ã  une date.
    Aucune mÃ©trique historique n'est calculÃ©e ici.
    """
    d = valuation_date or date_valo
    if not d:
        raise HTTPException(status_code=400, detail="date_valo/valuation_date obligatoire")
    tranche = index_type or mbi_tranche
    payload = _portfolio_snapshot_cached(str(d)[:10], _normalize_mbi_tranche(tranche), limit)
    return JSONResponse(
        content=payload,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


_PORTFOLIO_FREQS = frozenset({"daily", "weekly", "monthly", "quarterly", "yearly"})


def _portfolio_frequency_dates(start: date, end: date, frequency: str) -> list[date]:
    f = (frequency or "monthly").strip().lower()
    if f not in _PORTFOLIO_FREQS:
        raise ValueError("frequence invalide: daily, weekly, monthly, quarterly, yearly")
    out: list[date] = []
    d = start
    delta = {
        "daily": timedelta(days=1),
        "weekly": timedelta(days=7),
        "monthly": relativedelta(months=1),
        "quarterly": relativedelta(months=3),
        "yearly": relativedelta(years=1),
    }[f]
    while d <= end:
        out.append(d)
        d = d + delta
    if not out or out[-1] != end:
        out.append(end)
    return out


def _annual_factor(frequency: str) -> float:
    return {
        "daily": 252.0,
        "weekly": 52.0,
        "monthly": 12.0,
        "quarterly": 4.0,
        "yearly": 1.0,
    }.get((frequency or "monthly").strip().lower(), 12.0)


def _quantile_empirical(values: list[float], q: float) -> float:
    vals = sorted(float(v) for v in values if math.isfinite(float(v)))
    if not vals:
        return 0.0
    if len(vals) == 1:
        return vals[0]
    pos = max(0.0, min(1.0, q)) * (len(vals) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    w = pos - lo
    return vals[lo] * (1.0 - w) + vals[hi] * w


@lru_cache(maxsize=128)
def _portfolio_historical_available_dates_cached(start_iso: str, end_iso: str, courbe: str = "MAR_JJ") -> tuple[str, ...]:
    df = read_sql_dataframe(
        """
        SELECT DISTINCT date_courbe
        FROM dbo.histo_courbe_taux
        WHERE UPPER(LTRIM(RTRIM(courbe))) = UPPER(?)
          AND date_courbe BETWEEN ? AND ?
        ORDER BY date_courbe
        """,
        (courbe, start_iso, end_iso),
    )
    if df.empty or "DATE_COURBE" not in df.columns:
        return tuple()
    dates: list[str] = []
    for x in df["DATE_COURBE"].tolist():
        try:
            dates.append(pd.to_datetime(x).date().isoformat())
        except Exception:
            continue
    return tuple(sorted(set(dates)))


def _portfolio_group_key(d_iso: str, frequency: str) -> tuple[int, ...]:
    d = datetime.fromisoformat(str(d_iso)[:10]).date()
    f = (frequency or "monthly").strip().lower()
    if f == "daily":
        return (d.year, d.month, d.day)
    if f == "weekly":
        y, w, _ = d.isocalendar()
        return (int(y), int(w))
    if f == "monthly":
        return (d.year, d.month)
    if f == "quarterly":
        return (d.year, (d.month - 1) // 3 + 1)
    if f == "yearly":
        return (d.year,)
    raise ValueError("frequence invalide: daily, weekly, monthly, quarterly, yearly")


def _portfolio_display_series(daily_series: list[dict[str, Any]], frequency: str) -> list[dict[str, Any]]:
    if not daily_series:
        return []
    f = (frequency or "monthly").strip().lower()
    if f not in _PORTFOLIO_FREQS:
        raise ValueError("frequence invalide: daily, weekly, monthly, quarterly, yearly")
    selected: dict[tuple[int, ...], dict[str, Any]] = {}
    for row in daily_series:
        selected[_portfolio_group_key(str(row["date"]), f)] = row
    out = [deepcopy(row) for row in selected.values()]
    out.sort(key=lambda r: str(r["date"]))
    for i, row in enumerate(out):
        if i == 0:
            row["return"] = None
            row["pnl"] = None
            continue
        prev_idx = float(out[i - 1].get("index_base_100") or 0.0)
        idx = float(row.get("index_base_100") or 0.0)
        row["return"] = idx / prev_idx - 1.0 if prev_idx > 0.0 else None
        row["pnl"] = idx - prev_idx if prev_idx > 0.0 else None
    return out


def build_rebalanced_index_series(raw_snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Construit une serie d'indice rebalancée.

    Le rendement n'est pas la variation de NAV brute du bucket, car l'univers peut changer.
    On calcule donc un rendement moyen pondere sur les titres communs entre t-1 et t.
    Les entrants sont integres au panier du jour sans rendement artificiel, et les sortants
    sont consideres reinvestis dans le panier courant.
    """
    out: list[dict[str, Any]] = []
    prev_positions: dict[str, dict[str, float]] = {}
    index_level = 100.0
    peak_index = 100.0
    for i, snap in enumerate(raw_snapshots):
        positions = snap.get("_positions") or {}
        current_codes = set(positions.keys())
        previous_codes = set(prev_positions.keys())
        entries = sorted(current_codes - previous_codes)
        exits = sorted(previous_codes - current_codes)
        common = sorted(current_codes & previous_codes)
        if i == 0:
            ret = None
            pnl = None
        else:
            weighted_return = 0.0
            weight_sum = 0.0
            for code in common:
                p0 = float(prev_positions.get(code, {}).get("price") or 0.0)
                p1 = float(positions.get(code, {}).get("price") or 0.0)
                w0 = float(prev_positions.get(code, {}).get("weight") or 0.0)
                if p0 <= 0.0 or p1 <= 0.0 or w0 <= 0.0:
                    continue
                weighted_return += w0 * (p1 / p0 - 1.0)
                weight_sum += w0
            ret = weighted_return / weight_sum if weight_sum > 0.0 else 0.0
            index_level *= 1.0 + ret
            pnl = index_level - float(out[-1]["index_base_100"]) if out else None
        peak_index = max(peak_index, index_level)
        row = {k: v for k, v in snap.items() if k != "_positions"}
        row["raw_nav"] = float(row.get("nav") or 0.0)
        row["index_base_100"] = index_level
        row["return"] = ret
        row["pnl"] = pnl
        row["pnl_cumule"] = index_level - 100.0
        row["cumulative_return"] = index_level / 100.0 - 1.0
        row["drawdown"] = index_level / peak_index - 1.0 if peak_index > 0.0 else 0.0
        row["entries_count"] = len(entries)
        row["exits_count"] = len(exits)
        row["entries_codes"] = entries
        row["exits_codes"] = exits
        row["universe_turnover"] = (len(entries) + len(exits)) / max(int(row.get("number_of_bonds") or 0), 1)
        out.append(row)
        prev_positions = positions
    return out


@lru_cache(maxsize=32)
def _portfolio_history_payload_cached(
    start_iso: str,
    end_iso: str,
    tranche_key: str,
    frequency: str,
    limit_key: int,
    risk_free_rate: float,
) -> dict[str, Any]:
    d0 = datetime.fromisoformat(start_iso).date()
    d1 = datetime.fromisoformat(end_iso).date()
    limit = None if int(limit_key) < 0 else int(limit_key)
    available_dates = list(_portfolio_historical_available_dates_cached(start_iso, end_iso, "MAR_JJ"))
    raw_snapshots: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for d_iso in available_dates:
        try:
            snap = _portfolio_snapshot_cached(d_iso, tranche_key, limit)
            s = snap.get("summary") or {}
            nav = float(s.get("total_market_value") or 0.0)
            if nav <= 0.0:
                raise ValueError("NAV nulle")
            pos_map: dict[str, dict[str, float]] = {}
            for p in snap.get("positions") or []:
                code = str(p.get("code") or "").strip()
                if not code:
                    continue
                price = float(p.get("price") or 0.0)
                if price <= 0.0:
                    q = float(p.get("quantite") or 0.0)
                    mv = float(p.get("market_value") or 0.0)
                    price = mv / q if q > 0.0 else 0.0
                pos_map[code] = {
                    "price": price,
                    "market_value": float(p.get("market_value") or 0.0),
                    "weight": float(p.get("weight") or 0.0),
                }
            raw_snapshots.append(
                {
                    "date": d_iso,
                    "nav": nav,
                    "raw_nav": nav,
                    "index_base_100": 100.0,
                    "return": None,
                    "pnl": None,
                    "pnl_cumule": 0.0,
                    "cumulative_return": 0.0,
                    "drawdown": 0.0,
                    "duration": float(s.get("weighted_duration") or 0.0),
                    "sensibilite": float(s.get("weighted_sensibilite") or 0.0),
                    "convexite": float(s.get("weighted_convexite") or 0.0),
                    "ytm": float(s.get("weighted_ytm") or 0.0),
                    "spread": float(s.get("weighted_spread") or 0.0),
                    "dv01": float(s.get("portfolio_dv01") or 0.0),
                    "number_of_bonds": int(s.get("number_of_bonds") or 0),
                    "number_of_issuers": int(s.get("number_of_issuers") or 0),
                    "_positions": pos_map,
                }
            )
        except Exception as e:
            errors.append({"date": d_iso, "reason": str(e)})

    daily_series = build_rebalanced_index_series(raw_snapshots)
    first_date: date | None = None
    if daily_series:
        first_date = datetime.fromisoformat(str(daily_series[0]["date"])).date()

    series = _portfolio_display_series(daily_series, frequency)
    returns = [float(x["return"]) for x in daily_series if x.get("return") is not None]
    display_returns = [float(x["return"]) for x in series if x.get("return") is not None]
    pnl = [float(x["pnl"]) for x in series if x.get("pnl") is not None]
    observations = len(daily_series)
    stats_available = len(returns) >= 10
    vol = float(np.std(returns, ddof=1) * math.sqrt(252.0)) if stats_available and len(returns) > 1 else None
    cumulative = float(daily_series[-1]["cumulative_return"]) if daily_series else 0.0
    if daily_series and first_date is not None:
        last_date = datetime.fromisoformat(str(daily_series[-1]["date"])).date()
        years = max((last_date - first_date).days / 365.0, 1.0 / 365.0)
        annualized = (1.0 + cumulative) ** (1.0 / years) - 1.0 if (1.0 + cumulative) > 0 else None
    else:
        annualized = None
    sharpe = (
        (float(annualized) - float(risk_free_rate)) / float(vol)
        if stats_available and annualized is not None and vol is not None and abs(float(vol)) > 1e-15
        else None
    )
    max_drawdown = min((float(x.get("drawdown") or 0.0) for x in daily_series), default=0.0)
    losses = [-r for r in returns]
    last_nav = float(daily_series[-1]["nav"]) if daily_series else 0.0
    var_payload: dict[str, dict[str, float | None]] = {}
    for level in (0.90, 0.95, 0.99):
        var_pct = max(0.0, _quantile_empirical(losses, level)) if stats_available else None
        var_payload[str(int(level * 100))] = {
            "pct": var_pct,
            "amount": (var_pct * last_nav) if var_pct is not None else None,
        }
    quality_warning = None
    if observations == 0:
        quality_warning = "aucune date historique disponible dans dbo.histo_courbe_taux"
    elif len(returns) < 10:
        quality_warning = "observations insuffisantes pour VaR, Sharpe et volatilite annualisee"
    elif observations < 30:
        quality_warning = "historique faible"
    summary = {
        "start_date": d0.isoformat(),
        "end_date": d1.isoformat(),
        "actual_start_date": daily_series[0]["date"] if daily_series else None,
        "actual_end_date": daily_series[-1]["date"] if daily_series else None,
        "frequency": frequency,
        "observations": observations,
        "display_observations": len(series),
        "dates_available": len(available_dates),
        "dates_ignored": len(errors),
        "statistics_available": stats_available,
        "quality_warning": quality_warning,
        "calculation_method": "daily_rebalanced_index",
        "rebalanced": True,
        "risk_free_rate": risk_free_rate,
        "performance_cumulee": cumulative,
        "performance_annualisee": annualized,
        "volatilite_annualisee": vol,
        "sharpe_ratio": sharpe,
        "max_drawdown": max_drawdown,
        "tracking_error": 0.0,
        "information_ratio": 0.0,
        "pnl_total": float(sum(pnl)) if pnl else 0.0,
        "var": var_payload,
    }
    return {
        "mode": "history",
        "portfolio_name": _mbi_tranche_label(tranche_key),
        "mbi_tranche": tranche_key,
        "date_debut": d0.isoformat(),
        "date_fin": d1.isoformat(),
        "frequence": frequency,
        "source_dates": "dbo.histo_courbe_taux",
        "summary": summary,
        "series": series,
        "daily_series": daily_series,
        "returns": returns,
        "display_returns": display_returns,
        "errors": errors,
    }


@app.get("/api/portfolio/history-legacy-disabled")
def portfolio_history(
    date_debut: str,
    date_fin: str,
    mbi_tranche: str = "global",
    frequence: str = "monthly",
    limit: int | None = None,
):
    """
    Mode 2 — historique/performance : reconstruit dynamiquement le benchmark sur chaque date.
    Les dates sans courbe/taux disponibles sont ignorÃ©es et retournÃ©es dans ``errors``.
    """
    try:
        d0 = datetime.fromisoformat(str(date_debut)[:10]).date()
        d1 = datetime.fromisoformat(str(date_fin)[:10]).date()
    except Exception as e:
        raise HTTPException(status_code=400, detail="date_debut/date_fin invalides") from e
    if d1 < d0:
        raise HTTPException(status_code=400, detail="date_fin doit être >= date_debut")
    freq = (frequence or "monthly").strip().lower()
    try:
        dates = _portfolio_frequency_dates(d0, d1, freq)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    tranche_key = _normalize_mbi_tranche(mbi_tranche)
    if tranche_key not in _MBI_TRANCHES:
        raise HTTPException(status_code=400, detail=f"mbi_tranche invalide: {mbi_tranche!r}")

    series: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for d in dates:
        try:
            snap = _portfolio_snapshot_cached(d.isoformat(), tranche_key, limit)
            s = snap.get("summary") or {}
            nav = float(s.get("total_market_value") or 0.0)
            if nav <= 0.0:
                raise ValueError("NAV nulle")
            series.append(
                {
                    "date": d.isoformat(),
                    "nav": nav,
                    "return": None,
                    "pnl": None,
                    "cumulative_return": 0.0,
                    "duration": float(s.get("weighted_duration") or 0.0),
                    "sensibilite": float(s.get("weighted_sensibilite") or 0.0),
                    "convexite": float(s.get("weighted_convexite") or 0.0),
                    "ytm": float(s.get("weighted_ytm") or 0.0),
                    "spread": float(s.get("weighted_spread") or 0.0),
                    "dv01": float(s.get("portfolio_dv01") or 0.0),
                    "number_of_bonds": int(s.get("number_of_bonds") or 0),
                }
            )
        except Exception as e:
            errors.append({"date": d.isoformat(), "reason": str(e)})

    if len(series) >= 2:
        first_nav = float(series[0]["nav"])
        peak = first_nav
        for i in range(1, len(series)):
            prev_nav = float(series[i - 1]["nav"])
            nav = float(series[i]["nav"])
            ret = nav / prev_nav - 1.0 if prev_nav > 0 else 0.0
            series[i]["return"] = ret
            series[i]["pnl"] = nav - prev_nav
            series[i]["cumulative_return"] = nav / first_nav - 1.0 if first_nav > 0 else 0.0
            peak = max(peak, nav)
            series[i]["drawdown"] = nav / peak - 1.0 if peak > 0 else 0.0
        series[0]["drawdown"] = 0.0
    returns = [float(x["return"]) for x in series if x.get("return") is not None]
    pnl = [float(x["pnl"]) for x in series if x.get("pnl") is not None]
    ann = _annual_factor(freq)
    mean_ret = float(np.mean(returns)) if returns else 0.0
    vol = float(np.std(returns, ddof=1) * math.sqrt(ann)) if len(returns) > 1 else 0.0
    cumulative = float(series[-1]["cumulative_return"]) if series else 0.0
    annualized = (1.0 + cumulative) ** (ann / max(len(returns), 1)) - 1.0 if returns else 0.0
    sharpe = annualized / vol if vol > 1e-15 else 0.0
    max_drawdown = min((float(x.get("drawdown") or 0.0) for x in series), default=0.0)
    losses = [-r for r in returns]
    var_payload = {}
    last_nav = float(series[-1]["nav"]) if series else 0.0
    for level in (0.90, 0.95, 0.99):
        var_pct = max(0.0, _quantile_empirical(losses, level))
        var_payload[str(int(level * 100))] = {
            "pct": var_pct,
            "amount": var_pct * last_nav,
        }
    summary = {
        "start_date": d0.isoformat(),
        "end_date": d1.isoformat(),
        "frequency": freq,
        "observations": len(series),
        "performance_cumulee": cumulative,
        "performance_annualisee": annualized,
        "volatilite_annualisee": vol,
        "sharpe_ratio": sharpe,
        "max_drawdown": max_drawdown,
        "tracking_error": 0.0,
        "information_ratio": 0.0,
        "pnl_total": float(sum(pnl)) if pnl else 0.0,
        "var": var_payload,
    }
    return JSONResponse(
        content={
            "mode": "history",
            "portfolio_name": _mbi_tranche_label(tranche_key),
            "mbi_tranche": tranche_key,
            "date_debut": d0.isoformat(),
            "date_fin": d1.isoformat(),
            "frequence": freq,
            "summary": summary,
            "series": series,
            "returns": returns,
            "errors": errors,
        },
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.get("/api/portfolio/history")
def portfolio_history_sql_dates(
    date_debut: str | None = None,
    date_fin: str | None = None,
    mbi_tranche: str = "global",
    frequence: str = "monthly",
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    index_type: str | None = Query(default=None),
    display_frequency: str | None = Query(default=None),
    risk_free_rate: float = 0.0,
    limit: int | None = None,
):
    """
    Mode 2 - historique/performance.

    Les dates de pricing sont uniquement les dates reelles presentes dans dbo.histo_courbe_taux.
    La frequence sert seulement a choisir la derniere date disponible par periode pour l'affichage.
    """
    d_start = start_date or date_debut
    d_end = end_date or date_fin
    if not d_start or not d_end:
        raise HTTPException(status_code=400, detail="date_debut/start_date et date_fin/end_date obligatoires")
    try:
        d0 = datetime.fromisoformat(str(d_start)[:10]).date()
        d1 = datetime.fromisoformat(str(d_end)[:10]).date()
    except Exception as e:
        raise HTTPException(status_code=400, detail="date_debut/date_fin invalides") from e
    if d1 < d0:
        raise HTTPException(status_code=400, detail="date_fin doit etre >= date_debut")
    freq = (display_frequency or frequence or "monthly").strip().lower()
    if freq not in _PORTFOLIO_FREQS:
        raise HTTPException(status_code=400, detail="frequence invalide: daily, weekly, monthly, quarterly, yearly")
    tranche_key = _normalize_mbi_tranche(index_type or mbi_tranche)
    if tranche_key not in _MBI_TRANCHES:
        raise HTTPException(status_code=400, detail=f"mbi_tranche/index_type invalide: {index_type or mbi_tranche!r}")
    rfr = float(risk_free_rate or 0.0)
    if abs(rfr) > 1.0:
        rfr = rfr / 100.0
    payload = _portfolio_history_payload_cached(
        d0.isoformat(),
        d1.isoformat(),
        tranche_key,
        freq,
        int(limit) if limit is not None else -1,
        rfr,
    )
    return JSONResponse(
        content=payload,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.post("/api/obligations/base-titre-zc")
def valoriser_base_titre_zc(req: BaseTitreZcRequest | None = None):
    """
    Valorisation des lignes du fichier base titre oblig : prix dirty (taux secondaire + spread),
    duration de Macaulay, duration modifiée, convexité (YTM implicite), à partir de la courbe ZC.
    """
    root = Path(__file__).resolve().parent.parent
    req = req or BaseTitreZcRequest()
    zc_path = root / (req.courbe_zc_py or "pricing/curves/courbe_zc.py")
    try:
        courbe = charger_courbe_zc_depuis_fichier(zc_path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Courbe ZC: {e}") from e
    try:
        xlsx = resoudre_fichier_base_titre_oblig(root, req.excel_xlsx)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    try:
        df_in = _charger_base_titre_oblg_cache(xlsx)
        df_out, _det = valoriser_dataframe_base_titre(df_in, courbe, valuation_date=None)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Base titre: {e}") from e
    try:
        courbe_rel = str(zc_path.resolve().relative_to(root.resolve()))
    except ValueError:
        courbe_rel = str(zc_path)
    return {
        "fichier": str(xlsx.name),
        "courbe_zc": courbe_rel,
        "lignes": _df_to_records(df_out),
        "nb_lignes": len(df_out),
    }


@app.post("/api/bond")
def price_bond_endpoint(req: BondRequest):
    try:
        curve = _make_curve(req.curve)
        maturity_days = req.maturity_years * 365.0
        cf_df, summary = bond_valuation_report(
            curve,
            req.nominal,
            req.coupon_pct / 100.0,
            maturity_days,
            req.frequency,
        )
        cashflows = _df_to_records(cf_df) if not cf_df.empty else []
        metrics = _df_to_records(summary) if not summary.empty else []
        return {"cashflows": cashflows, "metrics": metrics}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
