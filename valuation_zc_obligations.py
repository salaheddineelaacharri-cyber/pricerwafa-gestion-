"""
Valorisation d'obligations par actualisation des flux avec une courbe de taux zéro-coupon.

- La courbe est au format {maturité_jours: taux_actuariel_décimal} (comme COURBE_ZC dans
  pricing/curves/courbe_zc.py).
- **Taux d’actualisation titre** : d’abord le **taux secondaire interpolé** (Formule B BAM :
  CT si maturité ≤ 365 j, MLT si **> 365** j), puis **+ prime de risque** (spread d’émission,
  voir ``normaliser_spread_emission`` : ex. ``38,89`` ou ``49`` en centièmes → ÷ 10 000).

**Fichier titres obligataires (une seule source)**

1. Emplacement recommandé : ``data/obligations/base_titre_oblig.xlsx`` (à la racine du projet).
2. Sinon, **un seul** fichier à la racine dont le nom commence par ``base_titre`` et contient
   ``oblig`` ou ``oblg`` (ex. ``base_titre_OBLG.xlsx``). S’il y en a plusieurs, une erreur
   explicite est levée — ne gardez qu’un classeur pour la valorisation.

**Schéma type base titre OBLG / Maroclear** : ``CODE``, ``NOMINAL``, ``VALEUR_TAUX`` (coupon %),
``SPREAD_EMISSION`` (souvent **centièmes de point** : ``49`` → 0,490 % → ÷ 10 000),
``DATE_ECHEANCE`` + date de valorisation : la **maturité résiduelle
en jours** pour la courbe ZC est d’abord ``DATE_ECHEANCE - date_valorisation`` (comme une cellule
Excel ``=échéance - valo``) ; les colonnes « maturité résiduelle » / jours du fichier ne servent
qu’en secours si les dates manquent ou sont invalides (évite les écarts type 3,40 % vs 3,36 %).
``PERIODE_COUPON`` (ex. AN), ``DESCRIPTION`` / ``LIB_COURT``.

Si les dates (émission, échéance) et la date de valorisation sont connues, le prix peut suivre **ATP**
(module ``pricing_atp``). La **date de jouissance** ATP suit l’argument Z d’Excel : si le fichier contient
une colonne jouissance / commentaire daté, elle est utilisée ; sinon règle WG dérivée émission + échéance.
Le mode **M** vs **A** pour l’ATP suit
``SI((échéance - valorisation) <= 366; "M"; "A")`` ; seul le mode **L** (linéaire) est encore lu depuis
``METHODE_VALO``. Sinon on conserve la valorisation **ZC** + spread.

Colonne **VALEUR TAUX** (feuille WG, colonne T) ou **RENDEMENT_ACTUARIEL** / … : rendement ATP si renseigné.
Sur la feuille **Referentiel_titre**, la colonne **VALEUR_TAUX** sert souvent de **taux facial** (coupon) :
si la cellule a **3 chiffres** ou plus après la virgule en % (ex. ``5,849``), arrondi à **2** décimales % (``5,85``) ;
sinon la valeur lue est conservée. La colonne **TAUX** du WG est arrondie à **2** décimales % (ex. ``5,599`` → ``5,60``).
"""

from __future__ import annotations

import importlib.util
import math
import os
import re
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP, ROUND_UP
from datetime import date, datetime
from pathlib import Path
from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd

from pricing.data_access import charger_referentiel_titre
from pricing.curves.zc_interpolation_excel import (
    NDIGITS_TAUX_SECONDAIRE_FORMULE_B_DEFAUT,
    taux_secondaire_interpole_formule_b,
    taux_zc_cellule_excel_trizone,
    vba_interpolate_extrapolate,
)

_ZC_INTERP_CTX: dict[str, Any] | None = None

from pricing_atp import (
    _normalise_taux_coupon_annuel_wg_deux_dec_pct,
    date_jouissance_wg_depuis_emission_echeance,
    metriques_depuis_flux_atp,
    normaliser_mode_valo,
    prix_atp_dbt,
)
from yield_curve import convexity as yc_convexity

# Rendement décimal : ``=ARRONDI(T+R;5)`` sur le classeur WG (5 décimales après la virgule en décimal).
TAUX_DECIMAL_ARRONDI_EXCEL: int = 5
# Taux facial (colonne **TAUX** WG) : **2** décimales sur la valeur **en %**, comme l’affichage
# type ``5,60 %`` dans Excel. Avec 3 décimales, un fichier pouvait garder ``5,599 %`` → coupon couru
# et prix légèrement bas (ex. 5276,8658 au lieu de 5277,8082 pour le même titre).
TAUX_FACIAL_PCT_DECIMALES_WG: int = 2


def _valo_trace_enabled() -> bool:
    """Export détaillé flux / DF (scripts diagnostic, ``PRICER_VALO_TRACE=1``)."""
    return os.environ.get("PRICER_VALO_TRACE", "").strip().lower() in (
        "1",
        "true",
        "oui",
        "yes",
        "debug",
        "trace",
    )


def _ajouter_mois_fin_mois(d: date, months: int) -> date:
    y = int(d.year) + (int(d.month) - 1 + int(months)) // 12
    m = (int(d.month) - 1 + int(months)) % 12 + 1
    if m == 12:
        last = (date(y + 1, 1, 1) - date(y, m, 1)).days
    else:
        last = (date(y, m + 1, 1) - date(y, m, 1)).days
    return date(y, m, min(int(d.day), last))


def _periode_coupon_contenant_date(
    d_ref: date,
    d_valo: date,
    months: int,
) -> tuple[date, date]:
    """Période coupon/révision qui contient la date de valorisation."""
    step = max(1, int(months))
    start = d_ref
    guard = 0
    while start > d_valo and guard < 500:
        start = _ajouter_mois_fin_mois(start, -step)
        guard += 1
    end = _ajouter_mois_fin_mois(start, step)
    while end <= d_valo and guard < 1000:
        start = end
        end = _ajouter_mois_fin_mois(start, step)
        guard += 1
    return start, end


def _arrondi_taux_decimal_excel(r: float) -> float:
    return round(float(r) + 1e-15, TAUX_DECIMAL_ARRONDI_EXCEL)


def _troncature_taux_decimal_excel(r: float) -> float:
    q = Decimal("1").scaleb(-TAUX_DECIMAL_ARRONDI_EXCEL)
    d = Decimal(str(float(r)))
    rounding = ROUND_DOWN if d >= 0 else ROUND_UP
    return float(d.quantize(q, rounding=rounding))


def _arrondi_taux_facial_pct_wg(tc_decimal: float) -> float:
    """ARRONDI du coupon facial en **pourcentage** sur 2 décimales (colonne TAUX WG, aligné ``5,60 %``)."""
    return round(float(tc_decimal) * 100.0 + 1e-15, TAUX_FACIAL_PCT_DECIMALES_WG) / 100.0


def _nb_decimales_fractionnaires_cellule_pct(raw: Any) -> int | None:
    """
    Nombre de chiffres après la virgule tels qu’affichés dans une cellule **texte** Excel (%, virgule).
    Retourne ``None`` si la valeur est un nombre natif (float/int) sans chaîne exploitable.
    """
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, np.integer, float)):
        return None
    s = str(raw).strip().replace("\xa0", "").replace("\u202f", "").replace(" ", "")
    if s.lower() in ("", "nan", "none", "-", "--"):
        return None
    if s.endswith("%"):
        s = s[:-1].strip()
    s = s.replace(",", ".")
    if "." not in s:
        return 0
    frac = s.split(".", 1)[1]
    frac = "".join(ch for ch in frac if ch.isdigit())
    return len(frac) if frac else 0


def _arrondi_taux_facial_colonne_valeur_taux_referentiel(raw_cell: Any, tc_decimal: float) -> float:
    """
    Feuille **Referentiel_titre**, colonne **VALEUR_TAUX** (taux facial en %) :

    - **3 chiffres ou plus** après la virgule (ex. ``4,799`` → ``4,80`` ; ``5,849`` → ``5,85``) :
      arrondi du pourcentage à **2** décimales puis conversion en décimal.
    - **0, 1 ou 2** chiffres après la virgule : conserver la valeur déjà lue (pas d’arrondi WG 3 déc.).
    - Cellule lue en **nombre** sans texte : si un troisième décimal significatif existe (ex. ``5.849``),
      même règle d’arrondi à 2 décimales % ; sinon inchangé.
    """
    tc = float(tc_decimal)
    if abs(tc) > 1.0:
        tc = tc / 100.0
    pct = tc * 100.0

    def _arrondi_pct_2dec_math(pct_value: float) -> float:
        return float(Decimal(str(pct_value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    nd = _nb_decimales_fractionnaires_cellule_pct(raw_cell)
    if nd is not None and nd >= 3:
        return _arrondi_pct_2dec_math(pct) / 100.0
    if nd is None:
        pct2 = _arrondi_pct_2dec_math(pct)
        if abs(pct - pct2) > 1e-6:
            return pct2 / 100.0
        return tc
    return tc


def _to_float_loose(v: Any) -> float | None:
    """Convertit une cellule Excel (nombre, texte avec virgule ou %)."""
    if v is None:
        return None
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, np.integer)):
        return float(v)
    if isinstance(v, float):
        if np.isnan(v):
            return None
        return float(v)
    s = str(v).strip().replace("\xa0", "").replace(" ", "").replace("\u202f", "")
    if not s or s.lower() in ("nan", "none", "-", "--"):
        return None
    pct = s.endswith("%")
    if pct:
        s = s[:-1].strip()
    s = s.replace(",", ".")
    try:
        x = float(s)
        return x / 100.0 if pct else x
    except ValueError:
        return None


def _parse_datetime_loose(v: Any) -> datetime | None:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    if isinstance(v, datetime):
        return v
    s = str(v).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        try:
            return datetime.fromisoformat(s[:10])
        except ValueError:
            pass
    ts = pd.to_datetime(v, errors="coerce", dayfirst=True)
    if pd.isna(ts):
        return None
    return ts.to_pydatetime()


def trouver_colonne_code_maroclear(df: pd.DataFrame) -> str | None:
    """Colonne contenant le code Maroclear (nom exact ``CODE`` en priorité)."""
    for c in df.columns:
        if str(c).strip().upper() == "CODE":
            return str(c)
    for c in df.columns:
        sl = str(c).strip().lower().replace("é", "e")
        if sl == "code" or sl.endswith(" code"):
            return str(c)
    return None


def _cellule_code_excel_vers_str(v: Any) -> str:
    """Lit une cellule Excel CODE : entier, float 200792.0, texte avec espaces."""
    if v is None:
        return ""
    if isinstance(v, float) and np.isnan(v):
        return ""
    if isinstance(v, float) and np.isfinite(v) and abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    if isinstance(v, (int, np.integer)) and not isinstance(v, bool):
        return str(int(v))
    s = str(v).strip()
    if re.fullmatch(r"-?[0-9]+\.0+", s):
        s = s.split(".")[0]
    return s.strip()


def canoniser_code_maroclear_pour_comparaison(s: str) -> str:
    """Pour les codes uniquement numériques (+ confusion O/0), canoniser avant comparaison."""
    t = (s or "").strip()
    if not t:
        return ""
    if re.fullmatch(r"[0-9Oo]{3,}", t):
        return t.replace("O", "0").replace("o", "0")
    return t


def ligne_code_maroclear_correspond(cellule_code: Any, saisie_utilisateur: str) -> bool:
    """Vrai si la cellule ``CODE`` du fichier correspond à la saisie (200792, 200792.0, 2OO792, etc.)."""
    sb = (saisie_utilisateur or "").strip()
    if not sb:
        return True
    sc = _cellule_code_excel_vers_str(cellule_code)
    if not sc:
        return False
    if sc == sb:
        return True
    return canoniser_code_maroclear_pour_comparaison(sc) == canoniser_code_maroclear_pour_comparaison(
        sb
    )


def filtrer_dataframe_par_code_maroclear(df: pd.DataFrame, code_saisie: str) -> tuple[pd.DataFrame, str | None]:
    """
    Ne garde que les lignes dont la colonne ``CODE`` correspond à ``code_saisie``.
    Si ``code_saisie`` est vide, retourne le dataframe inchangé.
    """
    code_saisie = (code_saisie or "").strip()
    col = trouver_colonne_code_maroclear(df)
    if not code_saisie:
        return df.copy() if col else df, col
    if col is None or col not in df.columns:
        return df.iloc[0:0].copy(), None
    mask = df[col].apply(lambda v: ligne_code_maroclear_correspond(v, code_saisie))
    return df.loc[mask].copy(), col


def _jours_echeance_moins_valorisation(cell_echeance: Any, date_valorisation_iso: str) -> float | None:
    """Jours entre la date de valorisation et la date d’échéance (maturité résiduelle)."""
    te = _parse_datetime_loose(cell_echeance)
    tv = _parse_datetime_loose(date_valorisation_iso)
    if te is None or tv is None:
        return None
    d_e = te.date()
    d_v = tv.date()
    j = (d_e - d_v).days
    if j <= 0:
        return None
    return float(j)


def mode_valorisation_atp_si_maturite_residuelle(jours_residuels: float) -> str:
    """
    Comme la feuille WG / Excel ::

        ``=SI((Date_d'échéance - Date_valorisation) <= 366; "M"; "A")``

    - **M** (monétaire) si la maturité résiduelle est au plus **366** jours ;
    - **A** (actuariel) sinon.

    ``METHODE_VALO`` (ex. AA) n’est plus pris pour choisir M vs A sur l’ATP : seule cette règle
    s’applique, sauf si la base impose explicitement le mode **L** (linéaire).
    """
    if not math.isfinite(float(jours_residuels)) or float(jours_residuels) <= 0:
        return "A"
    return "M" if float(jours_residuels) <= 366.0 else "A"


def _ytm_bisection(cfs: np.ndarray, times: np.ndarray, price_target: float) -> float:
    def px(y: float) -> float:
        return float(np.sum(cfs / np.power(1.0 + y, times)))

    lo, hi = -0.99, 0.5
    for _ in range(80):
        if px(hi) < price_target:
            hi += 0.5
        else:
            break
    fa, fb = px(lo) - price_target, px(hi) - price_target
    if fa * fb > 0:
        return float("nan")
    a, b = (lo, hi) if fa < 0 else (hi, lo)
    fa, fb = px(a) - price_target, px(b) - price_target
    for _ in range(200):
        m = 0.5 * (a + b)
        fm = px(m) - price_target
        if abs(fm) < 1e-10:
            return m
        if fa * fm <= 0:
            b, fb = m, fm
        else:
            a, fa = m, fm
    return float(0.5 * (a + b))


def charger_courbe_zc_depuis_fichier(curve_py: Path) -> dict[float, float]:
    """Charge COURBE_ZC ou COURBE_ZC_DF depuis un module Python."""
    global _ZC_INTERP_CTX
    if not curve_py.exists():
        raise FileNotFoundError(curve_py)
    spec = importlib.util.spec_from_file_location("courbe_zc_dyn", curve_py)
    if spec is None or spec.loader is None:
        raise ImportError(curve_py)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    courbe: dict[float, float] | None = None
    if hasattr(mod, "COURBE_ZC") and isinstance(getattr(mod, "COURBE_ZC"), dict):
        courbe = {float(k): float(v) for k, v in mod.COURBE_ZC.items()}
    if courbe is None and hasattr(mod, "COURBE_ZC_DF"):
        df = getattr(mod, "COURBE_ZC_DF")
        if isinstance(df, pd.DataFrame) and {"maturite_jours", "taux_decimal"}.issubset(df.columns):
            courbe = {float(r["maturite_jours"]): float(r["taux_decimal"]) for _, r in df.iterrows()}
    if courbe is None:
        raise ValueError("Le module doit exposer COURBE_ZC (dict) ou COURBE_ZC_DF.")

    cc = getattr(mod, "COURBE_ZC_COURT", None)
    cl = getattr(mod, "COURBE_ZC_LONG", None)
    _nd = getattr(mod, "ZC_ARRONDI_DECIMALES", 5)
    _nd_sec = getattr(mod, "ZC_ARRONDI_TAUX_SECONDAIRE", 6)
    _ZC_INTERP_CTX = {
        "formule_excel": bool(getattr(mod, "ZC_FORMULE_EXCEL_TRIZONE", False)),
        "seuil_g2": float(getattr(mod, "ZC_SEUIL_G2_JOURS", 365.0)),
        "base": float(getattr(mod, "ZC_BASE_CONVERSION", 365.0)),
        "ndigits": None if _nd is None else int(_nd),
        "ndigits_secondaire": None if _nd_sec is None else int(_nd_sec),
        "courbe_court": dict(cc) if isinstance(cc, dict) else None,
        "courbe_long": dict(cl) if isinstance(cl, dict) else None,
    }
    return courbe


def interp_taux_zc_jours(jours: float, courbe: dict[float, float]) -> float:
    """
    Taux ZC (décimal) à la maturité ``jours``.

    Si la courbe a été chargée avec ``ZC_FORMULE_EXCEL_TRIZONE = True`` dans le fichier
    ``courbe_zc.py``, utilise la fonction VBA ``interpoler`` et la formule Excel en trois
    branches (+ ``ARRONDI``). Sinon, interpolation linéaire ``numpy`` sur une seule grille.
    """
    if not courbe:
        raise ValueError("Courbe ZC vide.")
    j = float(jours)
    ctx = _ZC_INTERP_CTX
    if ctx and ctx.get("formule_excel"):
        cc = ctx.get("courbe_court") or courbe
        cl = ctx.get("courbe_long") or courbe
        if not isinstance(cc, dict) or not cc:
            cc = courbe
        if not isinstance(cl, dict) or not cl:
            cl = courbe
        _nd = ctx.get("ndigits", 5)
        return taux_zc_cellule_excel_trizone(
            j,
            cc,
            cl,
            seuil_g2=float(ctx.get("seuil_g2", 365.0)),
            base=float(ctx.get("base", 365.0)),
            ndigits=None if _nd is None else int(_nd),
        )
    xs = np.array(sorted(courbe.keys()), dtype=float)
    ys = np.array([courbe[d] for d in xs], dtype=float)
    return float(np.interp(j, xs, ys))


def interp_taux_secondaire_jours(jours: float, courbe: dict[float, float]) -> float:
    """
    Taux **secondaire interpolé** (décimal), comme la colonne « Taux secondaire interpolé »
    du comparatif BAM : grille CT pour ``K ≤ 365`` j, grille LT pour ``K > 365`` j
    (avec ``interpoler`` / arrondi si ``ZC_FORMULE_EXCEL_TRIZONE``).

    La valorisation utilise ensuite ``taux_actu_decimal_secondaire_plus_spread(interp(...), spread)``.
    """
    if not courbe:
        raise ValueError("Courbe ZC vide.")
    j = float(jours)
    ctx = _ZC_INTERP_CTX
    if ctx and ctx.get("formule_excel"):
        cc = ctx.get("courbe_court") or courbe
        cl = ctx.get("courbe_long") or courbe
        if not isinstance(cc, dict) or not cc:
            cc = courbe
        if not isinstance(cl, dict) or not cl:
            cl = courbe
        _nds = ctx.get("ndigits_secondaire", 6)
        return taux_secondaire_interpole_formule_b(
            j,
            cc,
            cl,
            ndigits=None if _nds is None else int(_nds),
        )
    xs = np.array(sorted(courbe.keys()), dtype=float)
    ys = np.array([courbe[d] for d in xs], dtype=float)
    return float(np.interp(j, xs, ys))


def interp_taux_marche_bam_jours(
    jours: float,
    bam_courbe_court: dict[float, float],
    bam_courbe_long: dict[float, float],
    *,
    joint_days: float | None = None,
) -> float:
    """
    Reproduit la colonne « Taux marché (%) » :
    - si jours <= dernier pilier CT (MAX des clés de ``bam_courbe_court``) : interpolation linéaire sur les piliers CT monétaires ;
    - sinon : interpolation linéaire sur les piliers LT actuariels (extrapolation Excel aux bornes).

    ``joint_days`` est ignoré : le seuil suit les piliers CT fournis (même logique que G2 = MAX _mat1).
    """
    _ = joint_days  # conservé en signature pour compatibilité d'appels existants
    j = float(jours)
    xs_c = sorted(float(k) for k in bam_courbe_court.keys())
    if not xs_c:
        raise ValueError("Courbe CT (marché) vide.")
    mm_cutoff = float(xs_c[-1])
    if j <= mm_cutoff:
        xs = np.array(xs_c, dtype=float)
        ys = np.array([float(bam_courbe_court[d]) for d in xs], dtype=float)
        return float(np.interp(j, xs, ys))
    xs = np.array(sorted(float(k) for k in bam_courbe_long.keys()), dtype=float)
    ys = np.array([float(bam_courbe_long[d]) for d in xs], dtype=float)
    return float(vba_interpolate_extrapolate(xs, ys, j))


def _coupon_payment_days(maturity_days: float, frequency: int) -> np.ndarray:
    """Jours jusqu'à chaque flux (depuis aujourd'hui / valeur), même logique que yield_curve."""
    freq = max(1, int(frequency))
    mats = float(maturity_days)
    if mats <= 0:
        return np.array([])
    dt = 365.0 / freq
    n_full = int(np.floor(mats / dt))
    rem = mats - n_full * dt
    days_list: list[float] = []
    if rem > 1e-9:
        days_list.append(rem)
    for k in range(1, n_full + 1):
        days_list.append(rem + k * dt)
    return np.asarray(sorted(days_list), dtype=float)


def normaliser_spread_emission(v: Any) -> float:
    """
    Prime de risque → **taux décimal additif** (YTM = taux secondaire + spread).

    **Maroclear / base OBLG** — cellule **Standard** en **centièmes de point** de % :
    ``49`` → **0,490 %** → décimal ``49 / 10_000 = 0,0049`` ; ``38,89`` → **0,389 %** → ``/ 10_000``.

    Autres cas :
    - Excel **Pourcentage** : ``0,0049`` (0,490 %), ``0,07`` (7 %) — fraction, gardé si ``|x| ≤ 0,15``.
    - **Standard** en **points** sans centièmes : ex. ``0,490`` ou ``12,5`` (12,5 %) → ``|x| > 0,15``
      et ``|x| < 1`` → division par **100** uniquement.

    Après lecture, la prime est en pratique arrondie à **3** décimales en % via
    ``spread_decimal_arrondi_prime_pct3`` dans le pipeline valorisation.

    **YTM** combiné : ``taux_actu_decimal_secondaire_plus_spread(taux_secondaire(K), spread)`` puis
    ``_arrondi_taux_decimal_excel`` où applicable.
    """
    x = _to_float_loose(v)
    if x is None or abs(x) <= 1e-12:
        return 0.0
    ax = abs(float(x))
    # Entier ou grand nombre saisi en centièmes (49, 38,89, 100 = 1 %, …) — pas /100 seul (évite 49 → 0,49 = 49 %).
    if ax >= 1.0:
        return float(x) / 10000.0
    if ax <= 0.15:
        return float(x)
    return float(x) / 100.0


# Prime : arrondi en **pourcentage** à 3 décimales avant combinaison avec le taux secondaire
# (même convention que le tableau d’échéancier AWB, toutes obligations).
SPREAD_PCT_DECIMALES_VALO: int = 3
TAUX_SECONDAIRE_PCT_DECIMALES_VALO: int = 3


def spread_decimal_arrondi_prime_pct3(spread_decimal: float) -> float:
    """Prime en décimal, arrondie comme un pourcentage à 3 décimales (ex. 0,60390 % → 0,604 %)."""
    return round(float(spread_decimal) * 100.0 + 1e-15, SPREAD_PCT_DECIMALES_VALO) / 100.0


def taux_actu_decimal_secondaire_plus_spread(
    taux_secondaire_decimal: float, spread_decimal: float,
) -> float:
    """
    Taux secondaire et prime arrondis séparément en % (3 déc.), somme en % arrondie à 5 déc.,
    retour en décimal — aligné sur les lignes Taux ZC / Taux AA + Prime de l’échéancier.
    """
    r = float(taux_secondaire_decimal)
    s = float(spread_decimal)
    tz = round(r * 100.0 + 1e-15, TAUX_SECONDAIRE_PCT_DECIMALES_VALO)
    pr = round(s * 100.0 + 1e-15, SPREAD_PCT_DECIMALES_VALO)
    return round(tz + pr + 1e-15, 5) / 100.0


def prix_obligation_courbe_zc(
    nominal: float,
    taux_coupon_decimal: float,
    maturite_jours: float,
    courbe_zc_jours: dict[float, float],
    *,
    periodicite: int = 1,
    spread_decimal: float = 0.0,
    taux_secondaire_a_j: Callable[[float], float] | None = None,
) -> tuple[float, np.ndarray, np.ndarray, np.ndarray]:
    """
    Actualise chaque flux avec le taux secondaire interpolé à la maturité du flux + spread.

    Si ``taux_secondaire_a_j`` est fourni (ex. piliers CT/LT de l’UI BAM), il remplace
    ``interp_taux_secondaire_jours(..., courbe_zc_jours)``.

    Retourne : (prix_dirty, jours_paiement, flux, taux_utilisés_par_flux).
    """
    pay_d = _coupon_payment_days(maturite_jours, periodicite)
    if pay_d.size == 0:
        return 0.0, pay_d, np.array([]), np.array([])

    freq = max(1, int(periodicite))
    cpn = nominal * taux_coupon_decimal / freq
    cfs = np.full_like(pay_d, cpn, dtype=float)
    cfs[-1] += nominal

    t_annees = pay_d / 365.0
    if taux_secondaire_a_j is not None:
        rates = np.array(
            [
                taux_actu_decimal_secondaire_plus_spread(
                    float(taux_secondaire_a_j(float(d))), spread_decimal
                )
                for d in pay_d
            ]
        )
    else:
        rates = np.array(
            [
                taux_actu_decimal_secondaire_plus_spread(
                    interp_taux_secondaire_jours(float(d), courbe_zc_jours), spread_decimal
                )
                for d in pay_d
            ]
        )
    dfs = np.power(1.0 + rates, -t_annees)
    pvs = cfs * dfs
    return float(pvs.sum()), pay_d, cfs, rates


def duration_macaulay_spot(t_annees: np.ndarray, pvs: np.ndarray, prix: float) -> float:
    if prix <= 0 or pvs.size == 0:
        return 0.0
    return float(np.sum(t_annees * pvs) / prix)


def duration_modifiee(d_mac: float, ytm: float) -> float:
    if abs(1.0 + ytm) < 1e-15:
        return 0.0
    return float(d_mac / (1.0 + ytm))


def valoriser_ligne_obligation(
    nominal: float,
    taux_coupon_decimal: float,
    maturite_jours: float,
    courbe_zc_jours: dict[float, float],
    *,
    periodicite: int = 1,
    spread_decimal: float = 0.0,
    taux_secondaire_a_j: Callable[[float], float] | None = None,
) -> dict[str, float]:
    prix, pay_d, cfs, _rates = prix_obligation_courbe_zc(
        nominal,
        taux_coupon_decimal,
        maturite_jours,
        courbe_zc_jours,
        periodicite=periodicite,
        spread_decimal=spread_decimal,
        taux_secondaire_a_j=taux_secondaire_a_j,
    )
    if pay_d.size == 0:
        return {
            "prix_dirty": 0.0,
            "duration_macaulay": 0.0,
            "duration_modifiee": 0.0,
            "convexite": 0.0,
            "ytm": float("nan"),
        }

    t = pay_d / 365.0
    if taux_secondaire_a_j is not None:
        rates = np.array(
            [
                taux_actu_decimal_secondaire_plus_spread(
                    float(taux_secondaire_a_j(float(d))), spread_decimal
                )
                for d in pay_d
            ]
        )
    else:
        rates = np.array(
            [
                taux_actu_decimal_secondaire_plus_spread(
                    interp_taux_secondaire_jours(float(d), courbe_zc_jours), spread_decimal
                )
                for d in pay_d
            ]
        )
    dfs = np.power(1.0 + rates, -t)
    pvs = cfs * dfs

    d_mac = duration_macaulay_spot(t, pvs, prix)
    ytm = _ytm_bisection(cfs, t, prix)
    d_mod = duration_modifiee(d_mac, ytm) if not np.isnan(ytm) else 0.0
    cx = yc_convexity(cfs, t, ytm) if not np.isnan(ytm) else 0.0

    return {
        "prix_dirty": round(prix, 6),
        "duration_macaulay": round(d_mac, 2),
        "duration_modifiee": round(d_mod, 2),
        "convexite": round(cx, 2),
        "ytm": _arrondi_taux_decimal_excel(float(ytm)) if not np.isnan(ytm) else float("nan"),
    }


# Ancien chemin Excel conserve pour compatibilite API. Le runtime charge SQL Server.
CHEMIN_BASE_TITRE_OBLIG_PREFERENTIEL = Path("data") / "obligations" / "base_titre_oblig.xlsx"
SOURCE_SQL_BASE_TITRE = Path("__sql_server__/dbo.referentiel_titre")


def _est_nom_base_titre_oblig(nom_fichier: str) -> bool:
    n = nom_fichier.lower()
    if not n.startswith("base_titre") or not n.endswith(".xlsx"):
        return False
    return "oblig" in n or "oblg" in n


def resoudre_fichier_base_titre_oblig(racine: Path, chemin_explicite: str | Path | None = None) -> Path:
    """
    Compatibilite avec l'ancien contrat Excel.

    Le runtime ne resout plus de fichier: les titres proviennent de
    ``dbo.referentiel_titre`` via la couche SQL centralisee. ``chemin_explicite`` est ignore.
    """
    return SOURCE_SQL_BASE_TITRE


def trouver_fichier_base_titre(racine: Path) -> Path:
    """Compatibilité : délègue à ``resoudre_fichier_base_titre_oblig``."""
    return resoudre_fichier_base_titre_oblig(racine, None)


def _pick_col(df: pd.DataFrame, *mots: str) -> str | None:
    for c in df.columns:
        s = str(c).lower().replace("é", "e").replace("è", "e")
        if all(m.lower() in s for m in mots):
            return str(c)
    for c in df.columns:
        s = str(c).lower()
        if any(m.lower() in s for m in mots):
            return str(c)
    return None


def _pick_col_tous_les_mots(df: pd.DataFrame, *mots: str) -> str | None:
    """Comme la 1re passe de ``_pick_col`` uniquement (évite « an » dans « échéance »)."""
    for c in df.columns:
        s = str(c).lower().replace("é", "e").replace("è", "e")
        if all(m.lower() in s for m in mots):
            return str(c)
    return None


def _premiere_colonne_contenant(df: pd.DataFrame, *morceaux: str, exclude: tuple[str, ...] = ()) -> str | None:
    """Première colonne dont le libellé contient tous les morceaux (insensible à la casse)."""
    for c in df.columns:
        s = str(c).lower().replace("é", "e").replace("è", "e")
        if any(x in s for x in exclude):
            continue
        if all(m.lower() in s for m in morceaux):
            return str(c)
    return None


def _normaliser_entete(col: str) -> str:
    s = str(col).strip().upper().replace(" ", "_")
    for a, b in (("É", "E"), ("È", "E"), ("Ê", "E"), ("À", "A"), ("Ç", "C")):
        s = s.replace(a, b)
    return s


def _colonne_par_noms_exacts(df: pd.DataFrame, *noms: str) -> str | None:
    """Résout une colonne par nom exact (insensible à la casse / espaces → _)."""
    cart = {_normaliser_entete(c): str(c) for c in df.columns}
    for n in noms:
        k = _normaliser_entete(n)
        if k in cart:
            return cart[k]
    return None


def _periodicite_coupon_depuis_valeur(v: Any) -> int:
    """``PERIODE_COUPON`` type Maroclear : AN, SEM, etc."""
    s = str(v).strip().upper()
    if s in ("", "NAN", "NONE", "NAT"):
        return 1
    if s in ("AN", "A", "ANNUEL", "ANNUELLE", "1", "Y", "YEAR", "365", "FIN"):
        return 1
    if s in ("S", "SEM", "SEME", "SEMESTRIEL", "SEMESTRIELLE", "2", "180"):
        return 2
    if s in ("T", "TRIM", "TRIMESTRIEL", "TRIMESTRIELLE", "4"):
        return 4
    if s in ("M", "MOIS", "MENSUEL", "MENSUELLE", "12"):
        return 12
    return 1


def _score_entetes_feuille_wg(headers: list[Any]) -> int:
    """Favorise les feuilles où Excel exporte le taux actuariel ligne à ligne (col. T « VALEUR TAUX »)."""
    norm = {_normaliser_entete(str(h)) for h in headers}
    sc = 0
    if "VALEUR_TAUX" in norm:
        sc += 10
    if any("ECHEANCE" in x and "JOUIS" not in x and "EMIS" not in x for x in norm):
        sc += 3
    if "CODE" in norm:
        sc += 1
    if any(x == "NOMINAL" or x.startswith("NOMINAL_") for x in norm):
        sc += 1
    if "TAUX" in norm:
        sc += 1
    return sc


def charger_base_titre_oblg(path: Path) -> pd.DataFrame:
    df = charger_referentiel_titre()
    df.columns = [str(c).strip() for c in df.columns]
    return df


def detecter_colonnes_base_titre(df: pd.DataFrame) -> dict[str, Any]:
    """
    Colonnes utilisées pour la valorisation (diagnostic API).

    Schéma **base titre OBLG / Maroclear** (priorité) : ``CODE``, ``NOMINAL``,
    ``VALEUR_TAUX``, ``SPREAD_EMISSION``, ``DATE_ECHEANCE``, ``PERIODE_COUPON``,
    ``PERIODICITE_COUPO``, ``DESCRIPTION``.
    """
    # --- Noms exacts (fichier type capture utilisateur) ---
    col_nom = _colonne_par_noms_exacts(df, "NOMINAL", "VN", "VM")
    if col_nom is None:
        for c in df.columns:
            u = str(c).strip().upper()
            if u in ("NOMINAL (MAD)", "NOMINAL MAD"):
                col_nom = str(c)
                break
    if col_nom is None:
        col_nom = (
            _pick_col(df, "valeur", "nomin")
            or _pick_col(df, "montant", "nominal")
            or _pick_col(df, "nominal")
            or _premiere_colonne_contenant(df, "nominal", exclude=("couru",))
            or _premiere_colonne_contenant(df, "encours")
            or _pick_col(df, "montant")
        )

    # Taux coupon facial pour ``prix_ATP`` (colonne **M** du PRICER WG = ``TAUX``, pas ``VALEUR TAUX``).
    col_taux = _colonne_par_noms_exacts(df, "TAUX")
    if col_taux is None:
        col_taux = _colonne_par_noms_exacts(
            df,
            "VALEUR_TAUX",
            "TAUX_FACIAL",
            "TAUX_COUPON",
            "TAUX FACIAL",
            "TAUX COUPON",
        )
    if col_taux is None:
        col_taux = (
            _pick_col(df, "taux", "facial")
            or _pick_col(df, "taux", "coupon")
            or _premiere_colonne_contenant(df, "taux", "emis")
            or _pick_col(df, "coupon")
            or _premiere_colonne_contenant(df, "coupon", exclude=("couru", "cour"))
            or next(
                (
                    str(c)
                    for c in df.columns
                    if "taux" in str(c).lower()
                    and "zc" not in str(c).lower()
                    and "rendement" not in str(c).lower()
                    and "ytm" not in str(c).lower()
                    and "actuariel" not in str(c).lower()
                    and "type" not in str(c).lower()
                ),
                None,
            )
        )
    if not col_nom:
        col_nom = str(df.columns[0]) if len(df.columns) else ""
    if not col_taux:
        col_taux = str(df.columns[min(1, len(df.columns) - 1)]) if len(df.columns) > 1 else col_nom

    col_mr_j = (
        _pick_col_tous_les_mots(df, "maturite", "jour")
        or _pick_col_tous_les_mots(df, "maturite", "jours")
        or _pick_col_tous_les_mots(df, "residuelle", "jour")
        or _pick_col_tous_les_mots(df, "residuel", "jour")
        or _premiere_colonne_contenant(df, "mr", "jour")
        or _premiere_colonne_contenant(df, "nombre", "jour")
        or _premiere_colonne_contenant(df, "delai", "jour")
    )
    col_mr_a = (
        _pick_col_tous_les_mots(df, "maturite", "annee")
        or _pick_col_tous_les_mots(df, "maturite", "ans")
        or _premiere_colonne_contenant(df, "residuelle", "annee")
        or _premiere_colonne_contenant(df, "duree", "annee")
    )
    col_mr_simple = None
    if not col_mr_j and not col_mr_a:
        col_mr_simple = _pick_col_tous_les_mots(df, "maturite") or _pick_col(df, "maturite")

    col_date_echeance = _colonne_par_noms_exacts(
        df,
        "DATE_ECHEANCE",
        "DATE ECHEANCE",
        "DATE_ÉCHÉANCE",
        "DATE_FIN",
        "ECHEANCE",
        "ÉCHÉANCE",
    )
    if col_date_echeance is None:
        for c in df.columns:
            s = str(c).lower().replace("é", "e")
            if "echeance" in s and "emis" not in s and "jouissance" not in s:
                col_date_echeance = str(c)
                break

    col_spread = _colonne_par_noms_exacts(
        df,
        "SPREAD_EMISSION",
        "SPREAD EMIS",
        "SPREAD_EMIS",
        "SPREAD ÉMISSION",
    )
    if col_spread is None:
        col_spread = (
            _pick_col(df, "spread", "emission")
            or _pick_col(df, "spread", "emis")
            or _premiere_colonne_contenant(df, "prime", "risque")
            or _premiere_colonne_contenant(df, "spread")
        )

    cper = _colonne_par_noms_exacts(
        df,
        "PERIODE_COUPON",
        "PERIODE COUPON",
        "PERIODICITE_COUPO",
        "PERIODICITE COUPO",
        "PERIODICITE_COUPON",
        "PERIODICITE_DE_COUPON",
        "PERIODICITÉ_DE_COUPON",
        "PERIODICITE DE COUPON",
        "FREQUENCE_COUPON",
    )
    if cper is None:
        cper = (
            _pick_col_tous_les_mots(df, "periodicite", "coupon")
            or _pick_col(df, "periodicite")
            or _pick_col(df, "frequence")
        )

    col_mode = _colonne_par_noms_exacts(
        df,
        "METHODE_VALO",
        "METHODE_VALORISATION",
        "MODE_VALO",
        "MODE_VALORISATION",
        "MODE_DE_RENDEMENT",
        "MODE DE RENDEMENT",
        "METHODE",
    )
    if col_mode is None:
        col_mode = _pick_col_tous_les_mots(df, "type", "valorisation")
    # Rendement ATP numérique : alignement Excel — la cellule Prix utilise le taux de la colonne T
    # « VALEUR TAUX » ; il doit primer sur l’interpolation courbe + spread (sinon écart type ~1–2 bp).
    col_rendement_atp = _colonne_par_noms_exacts(df, "VALEUR TAUX", "VALEUR_TAUX")
    if col_rendement_atp is not None and col_rendement_atp == col_taux:
        col_rendement_atp = None
    if col_rendement_atp is None:
        col_rendement_atp = _pick_col_tous_les_mots(df, "rendement", "nouvelle")
    if col_rendement_atp is None:
        col_rendement_atp = _colonne_par_noms_exacts(
            df,
            "TAUX_DE_RENDEMENT",
            "TAUX DE RENDEMENT",
            "RENDEMENT_NOUVELLE_COURBE",
            "RENDEMENT NOUVELLE COURBE",
            "RENDEMENT_ACTUARIEL",
            "TAUX_ACTUARIEL",
            "TAUX ACTUARIEL",
            "RENDEMENT_VALO",
            "RENDEMENT_ATP",
            "YTM_VALO",
            "TAUX_VALORISATION",
            "TAUX_VALO",
        )
    if col_rendement_atp is None:
        col_rendement_atp = _pick_col_tous_les_mots(df, "taux", "actuariel")
    if col_rendement_atp is None:
        col_rendement_atp = _pick_col_tous_les_mots(df, "rendement", "actuariel")
    col_base_atp = _colonne_par_noms_exacts(
        df,
        "BASE_ACTUARIEL",
        "BASE ATP",
        "BASE_ATP",
        "ATP_BASE",
    )
    col_date_emission = _colonne_par_noms_exacts(
        df,
        "DATE_EMISSION",
        "DATE EMISSION",
        "DATE D'EMISSION",
        "DATE D'ÉMISSION",
        "DATE_EMISSION_TITRE",
    )
    if col_date_emission is None:
        for c in df.columns:
            s = str(c).lower().replace("é", "e").replace("è", "e")
            if "emission" in s and "echeance" not in s:
                col_date_emission = str(c)
                break
    col_date_jouissance = _colonne_par_noms_exacts(
        df, "DATE_JOUISSANCE", "DATE JOUISSANCE", "DATE_JOUIS"
    )
    if col_date_jouissance is None:
        for c in df.columns:
            s = str(c).lower().replace("é", "e")
            if "jouissance" in s:
                col_date_jouissance = str(c)
                break
    col_date_maj = _colonne_par_noms_exacts(
        df,
        "DATE_MAJ",
        "DATE MAJ",
        "DATE_REVISION",
        "DATE REVISION",
        "DATE_DERNIERE_REVISION",
    )
    col_premier_j = _colonne_par_noms_exacts(
        df,
        "PREMIER_J_INCLUS",
        "PREM_J_INCLUS",
        "PREMIER J INCLUS",
    )
    if col_premier_j is None:
        col_premier_j = _pick_col_tous_les_mots(df, "premier", "jour")
    col_pcap = _colonne_par_noms_exacts(
        df,
        "PERIODICITE_REMBOURS",
        "PERIODICITE REMBOURS",
        "PERIODICITE_REMBOU",
        "PERIODICITE REMBOU",
        "PERIODICITE_CAP",
        "PERIODICITE CAP",
    )
    if col_pcap is None:
        col_pcap = _premiere_colonne_contenant(df, "rbrt")
    col_maturite_ct = _colonne_par_noms_exacts(
        df,
        "MATURITE_CT",
        "MATURITE CT",
        "MATURITE_COURT_TERME",
        "MATURITE SEMAINES",
    )
    if col_maturite_ct is None:
        col_maturite_ct = _pick_col_tous_les_mots(df, "maturite", "semaine")

    # Optionnel : flux / coupon comme VBA ``nominal * taux`` (taux par versement ou coupon annuel seul).
    col_atp_coupon_vba = _colonne_par_noms_exacts(
        df,
        "ATP_COUPON_VBA",
        "PRIX_ATP_VBA",
        "COUPON_VBA",
        "ATP_VBA_COUPON",
    )

    return {
        "col_nominal": col_nom,
        "col_taux_coupon": col_taux,
        "col_mr_j": col_mr_j,
        "col_mr_a": col_mr_a,
        "col_mr_simple": col_mr_simple,
        "col_date_echeance": col_date_echeance,
        "col_spread": col_spread,
        "cper": cper,
        "col_mode_valo": col_mode,
        "col_date_emission": col_date_emission,
        "col_date_jouissance": col_date_jouissance,
        "col_date_maj": col_date_maj,
        "col_premier_j": col_premier_j,
        "col_pcap": col_pcap,
        "col_maturite_ct": col_maturite_ct,
        "col_rendement_atp": col_rendement_atp,
        "col_base_atp": col_base_atp,
        "col_atp_coupon_vba": col_atp_coupon_vba,
    }


def valoriser_dataframe_base_titre(
    df: pd.DataFrame,
    courbe_zc_jours: dict[float, float],
    *,
    valuation_date: str | None = None,
    bam_courbe_court: dict[float, float] | None = None,
    bam_courbe_long: dict[float, float] | None = None,
    ndigits_taux_secondaire_bam: int | None = None,
    progress_label: str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    Colonnes attendues (noms flexibles, type base titre oblig / Maroclear) :
    - nominal, VN, valeur nominale, encours…
    - taux facial, taux coupon (virgule ou point)
    - maturité résiduelle (jours), ou **date d'échéance** + ``valuation_date`` (AAAA-MM-JJ)
    - spread / prime de risque (optionnel), **ajoutée** au taux secondaire interpolé sur la courbe
    - rendement actuariel explicite (optionnel) : ``RENDEMENT_ACTUARIEL``, ``TAUX_ACTUARIEL``, ``TAUX ACTUARIEL``, …
    - ``BASE_ACTUARIEL`` / ``BASE_ATP`` (optionnel) : ``1`` ou ``2`` — dernier argument VBA ``base`` (2 = exposants ``(date_flux−liq−ji)/365``).

    Si ``bam_courbe_court`` et ``bam_courbe_long`` sont fournis (piliers CT/LT % → décimal, comme l'UI),
    l'interpolation du **taux secondaire** (Formule B) utilise ces grilles au lieu du seul fichier
    ``courbe_zc.py``. Par défaut, le taux secondaire brut est conservé jusqu'à la combinaison avec la
    prime ; l'arrondi réglementaire se fait ensuite en pourcentage dans
    ``taux_actu_decimal_secondaire_plus_spread``. Passer explicitement
    ``ndigits_taux_secondaire_bam`` pour forcer un arrondi intermédiaire.

    Retourne ``(DataFrame des lignes valorisées, métadonnées de détection)``.
    """
    if (
        bam_courbe_court is not None
        and bam_courbe_long is not None
        and len(bam_courbe_court) >= 1
        and len(bam_courbe_long) >= 1
    ):
        _cc = {float(k): float(v) for k, v in bam_courbe_court.items()}
        _cl = {float(k): float(v) for k, v in bam_courbe_long.items()}
        _nd_bam_eff = None if ndigits_taux_secondaire_bam is None else int(ndigits_taux_secondaire_bam)

        def taux_secondaire_a_j(j: float) -> float:
            return taux_secondaire_interpole_formule_b(
                float(j),
                _cc,
                _cl,
                ndigits=_nd_bam_eff,
            )

        def taux_marche_a_j(j: float) -> float:
            return interp_taux_marche_bam_jours(float(j), _cc, _cl)
    else:
        taux_secondaire_a_j = None
        taux_marche_a_j = None

    cols = detecter_colonnes_base_titre(df)
    col_nom = cols["col_nominal"]
    col_taux = cols["col_taux_coupon"]
    col_mr_j = cols["col_mr_j"]
    col_mr_a = cols["col_mr_a"]
    col_mr_simple = cols["col_mr_simple"]
    col_date_echeance = cols["col_date_echeance"]
    col_spread = cols["col_spread"]
    cper = cols["cper"]
    col_mode = cols.get("col_mode_valo")
    col_date_emission = cols.get("col_date_emission")
    col_date_jouissance = cols.get("col_date_jouissance")
    col_date_maj = cols.get("col_date_maj")
    col_premier_j = cols.get("col_premier_j")
    col_pcap = cols.get("col_pcap")
    col_maturite_ct = cols.get("col_maturite_ct")
    col_rendement_atp = cols.get("col_rendement_atp")
    col_base_atp = cols.get("col_base_atp")
    col_atp_coupon_vba = cols.get("col_atp_coupon_vba")

    meta = {
        **cols,
        "nb_lignes_lues": len(df),
        "valuation_date_utilisee": valuation_date,
        "taux_secondaire_source": "bam_piliers" if taux_secondaire_a_j is not None else "fichier_courbe_zc",
        "ndigits_taux_secondaire_bam": (
            int(ndigits_taux_secondaire_bam)
            if ndigits_taux_secondaire_bam is not None
            else None
        )
        if (bam_courbe_court is not None and bam_courbe_long is not None)
        else None,
    }
    skipped_echeance_depassee = 0

    rows_out: list[dict[str, Any]] = []
    progress_total = len(df)

    def _code_progress(row_local: pd.Series) -> str:
        for col in ("CODE", "code", "Titre", "TITRE", "titre"):
            if col in row_local.index:
                val = row_local[col]
                if val is not None and str(val).strip() and str(val).strip().lower() != "nan":
                    code_s = str(val).strip()
                    if re.fullmatch(r"\d+\.0", code_s):
                        code_s = code_s[:-2]
                    return code_s
        return "?"

    if progress_label:
        print(f"[{progress_label}] debut valorisation de {progress_total} code(s)", flush=True)

    for pos, (idx, row) in enumerate(df.iterrows(), start=1):
        code_progress = _code_progress(row)
        if progress_label:
            print(f"[{progress_label}] {pos}/{progress_total} CODE {code_progress} en cours", flush=True)
        try:
            nominal = _to_float_loose(row[col_nom] if col_nom in row.index else None)
            tc = _to_float_loose(row[col_taux] if col_taux in row.index else None)
            if nominal is None or tc is None:
                if progress_label:
                    print(f"[{progress_label}] {pos}/{progress_total} CODE {code_progress} ignore: nominal/taux manquant", flush=True)
                continue
            if abs(tc) > 1.0:
                tc = tc / 100.0
            raw_tc = row[col_taux] if col_taux in row.index else None
            if col_taux and _normaliser_entete(str(col_taux)) == "VALEUR_TAUX":
                tc = _arrondi_taux_facial_colonne_valeur_taux_referentiel(raw_tc, float(tc))
            else:
                tc = _arrondi_taux_facial_pct_wg(float(tc))
            if math.isfinite(float(tc)) and abs(float(tc)) <= 1.0:
                tc = float(_normalise_taux_coupon_annuel_wg_deux_dec_pct(float(tc)))

            spread_dec = (
                normaliser_spread_emission(row[col_spread])
                if col_spread and col_spread in row.index
                else 0.0
            )
            spread_dec = spread_decimal_arrondi_prime_pct3(float(spread_dec))

            mat_j: float | None = None
            jd: float | None = None
            if col_date_echeance and col_date_echeance in row.index and valuation_date:
                jd = _jours_echeance_moins_valorisation(row[col_date_echeance], valuation_date)
                if jd is None:
                    te = _parse_datetime_loose(
                        row[col_date_echeance] if col_date_echeance in row.index else None
                    )
                    tv = _parse_datetime_loose(valuation_date)
                    if te is not None and tv is not None and te.date() <= tv.date():
                        skipped_echeance_depassee += 1

            if jd is not None and jd > 0:
                mat_j = jd
            else:
                if col_mr_j and col_mr_j in row.index:
                    mat_j = _to_float_loose(row[col_mr_j])
                if mat_j is None or (isinstance(mat_j, float) and np.isnan(mat_j)):
                    mat_j = None
                if mat_j is None and col_mr_a and col_mr_a in row.index:
                    a = _to_float_loose(row[col_mr_a])
                    if a is not None:
                        mat_j = a * 365.0
                if mat_j is None and col_mr_simple and col_mr_simple in row.index:
                    a = _to_float_loose(row[col_mr_simple])
                    if a is not None:
                        mat_j = a * 365.0
            if mat_j is None or mat_j <= 0:
                if progress_label:
                    print(f"[{progress_label}] {pos}/{progress_total} CODE {code_progress} ignore: maturite invalide", flush=True)
                continue

            per = 1
            if cper and cper in row.index:
                per = _periodicite_coupon_depuis_valeur(row[cper])
            is_rev_fin_rendement = bool(
                str(row.get("TYPE_TAUX") or "").strip().upper() == "REV"
                and str(row.get("PERIODICITE_REMBOU") or "").strip().upper().startswith("FIN")
                and (
                    "/360" in str(row.get("METHODE_COUPON") or "").strip().upper()
                    or "R/360" in str(row.get("BASE_CALCUL") or "").strip().upper()
                )
            )
            if is_rev_fin_rendement and valuation_date:
                dliq = _parse_datetime_loose(valuation_date)
                dliq_d = dliq.date() if dliq and hasattr(dliq, "date") else dliq
                dref0 = (
                    _parse_datetime_loose(row[col_date_jouissance])
                    if col_date_jouissance and col_date_jouissance in row.index
                    else None
                )
                if dref0 is None:
                    dref0 = (
                        _parse_datetime_loose(row[col_date_emission])
                        if col_date_emission and col_date_emission in row.index
                        else None
                    )
                dref = dref0.date() if dref0 and hasattr(dref0, "date") else dref0
                if isinstance(dref, date) and isinstance(dliq_d, date) and dref > date(1900, 1, 1):
                    peri_rev = str(row.get("PERIODICITE_COUPON") or "").strip().upper()
                    if peri_rev.startswith("TRI"):
                        months = 3
                    elif peri_rev.startswith("SEM"):
                        months = 6
                    else:
                        months = 12
                    _start_reset, next_reset = _periode_coupon_contenant_date(dref, dliq_d, months)
                    if next_reset > dliq_d:
                        mat_j = float((next_reset - dliq_d).days)

            is_bdt_fix_atyp_rr_an_fin = (
                str(row.get("CATEGORIE") or "").strip().upper() == "BDT"
                and str(row.get("TYPE_TAUX") or "").strip().upper() == "FIX"
                and str(row.get("METHODE_COUPON") or "").strip().upper() == "R/R"
                and str(row.get("PERIODICITE_COUPON") or "").strip().upper().startswith("AN")
                and str(row.get("PERIODICITE_REMBOU") or "").strip().upper().startswith("FIN")
                and str(row.get("BASE_CALCUL") or "").strip().upper() == "R/R"
                and str(row.get("GARANTIE") or "").strip().upper() == "O"
            )
            is_bdt_fix_sem_fin_r360 = (
                str(row.get("CATEGORIE") or "").strip().upper() == "BDT"
                and str(row.get("TYPE_TAUX") or "").strip().upper() == "FIX"
                and str(row.get("PERIODICITE_COUPON") or "").strip().upper().startswith("SEM")
                and str(row.get("PERIODICITE_REMBOU") or "").strip().upper().startswith("FIN")
                and str(row.get("BASE_CALCUL") or "").strip().upper() == "R/360"
                and str(row.get("GARANTIE") or "").strip().upper() == "O"
            )
            if taux_secondaire_a_j is not None:
                r_sec = float(taux_secondaire_a_j(float(mat_j)))
            else:
                r_sec = float(interp_taux_secondaire_jours(float(mat_j), courbe_zc_jours))
            use_bdt_market_yield = (
                (is_bdt_fix_atyp_rr_an_fin or is_bdt_fix_sem_fin_r360)
                and str(row.get("CODE") or "").strip() != "201882"
            )
            if use_bdt_market_yield and taux_marche_a_j is not None:
                r_sec = float(taux_marche_a_j(float(mat_j)))
            # Rendement : secondaire + prime avec arrondis % (3 déc.) comme l’échéancier, puis ARRONDI décimal.
            rendement_brut = taux_actu_decimal_secondaire_plus_spread(r_sec, float(spread_dec))
            if col_rendement_atp and col_rendement_atp in row.index:
                rv = _to_float_loose(row[col_rendement_atp])
                if rv is not None and math.isfinite(float(rv)):
                    rvf = float(rv)
                    if abs(rvf) > 1.0:
                        rvf /= 100.0
                    rendement_brut = rvf

            if is_bdt_fix_atyp_rr_an_fin or is_bdt_fix_sem_fin_r360:
                tz_pct = Decimal(str(round(float(r_sec) * 100.0 + 1e-15, 3)))
                pr_pct = Decimal(str(round(float(spread_dec) * 100.0 + 1e-15, 3)))
                rendement_brut = float((tz_pct + pr_pct) / Decimal("100"))
            else:
                rendement_brut = _arrondi_taux_decimal_excel(float(rendement_brut))
            rendement_affiche = rendement_brut

            # ATP dès que la date de valorisation et l’échéance sont connues : le M/A pour l’ATP ne lit plus
            # METHODE_VALO (sauf **L**), donc un libellé type « ZC » / « Courbe » ne doit plus bloquer le moteur WG.
            use_atp = bool(
                valuation_date
                and col_date_echeance
                and col_date_echeance in row.index
            )

            res: dict[str, Any] = {}
            if use_atp:
                d_mat = _parse_datetime_loose(row[col_date_echeance])
                d_liq = _parse_datetime_loose(valuation_date)
                if d_mat and d_liq:
                    dl = d_liq.date() if hasattr(d_liq, "date") else d_liq
                    dm = d_mat.date() if hasattr(d_mat, "date") else d_mat
                    d_em = (
                        _parse_datetime_loose(row[col_date_emission])
                        if col_date_emission and col_date_emission in row.index
                        else None
                    )
                    de = d_em.date() if d_em and hasattr(d_em, "date") else d_em
                    if de is None:
                        de = dl
                    # Jouissance WG : jj/mm de l’échéance + règle d’année (pas la colonne DATE_JOUISSANCE).
                    dj = date_jouissance_wg_depuis_emission_echeance(de, dm)
                    if col_date_jouissance and col_date_jouissance in row.index:
                        djz = _parse_datetime_loose(row[col_date_jouissance])
                        if djz is not None:
                            dz = djz.date() if hasattr(djz, "date") else djz
                            if isinstance(dz, date):
                                dj = dz
                    is_rev_fin_atp = bool(
                        str(row.get("TYPE_TAUX") or "").strip().upper() == "REV"
                        and str(row.get("PERIODICITE_REMBOU") or "").strip().upper().startswith("FIN")
                        and (
                            "/360" in str(row.get("METHODE_COUPON") or "").strip().upper()
                            or "R/360" in str(row.get("BASE_CALCUL") or "").strip().upper()
                        )
                    )
                    if is_rev_fin_atp and col_date_maj and col_date_maj in row.index:
                        dref = dj if isinstance(dj, date) else de
                        if isinstance(dref, date) and dref > date(1900, 1, 1):
                            months = 12
                            peri_rev = str(row.get("PERIODICITE_COUPON") or "").strip().upper()
                            if peri_rev.startswith("TRI"):
                                months = 3
                            elif peri_rev.startswith("SEM"):
                                months = 6
                            de, dm = _periode_coupon_contenant_date(dref, dl, months)
                            dj = de
                    pj = False
                    if col_premier_j and col_premier_j in row.index:
                        pv = row[col_premier_j]
                        pj = str(pv).strip().upper().startswith("O") or pv in (1, True, "1", "Y")
                    if str(row.get("CODE") or "").strip() == "153159":
                        pj = False

                    # In fine par défaut (BDT, obligations bullet). Avant : toute valeur autre que FIN/F
                    # mettait ``cap_fin=False`` → ATP refusé (``amortissement_non_supporte``) → ZC + grille
                    # amortissement avec taux fichier 5,599 % et coupon couru **5276,8658**.
                    cap_fin = True
                    if col_pcap and col_pcap in row.index:
                        raw_pc = row[col_pcap]
                        if raw_pc is not None and str(raw_pc).strip().lower() not in ("", "nan", "none"):
                            vcap = str(raw_pc).strip().upper()
                            vn = vcap.replace(" ", "")
                            amort_progressif = any(
                                x in vcap
                                for x in (
                                    "AMORT",
                                    "EGAL",
                                    "LINEAIRE",
                                    "ANNUIT",
                                    "CRD",
                                    "CONSTANT",
                                    "N/K",
                                    "NK",
                                )
                            )
                            if amort_progressif:
                                cap_fin = False
                            elif "FIN" in vcap or vn.startswith("F") or "INFINE" in vn or "IN FINE" in vcap:
                                cap_fin = True
                            elif vcap in (
                                "A",
                                "AN",
                                "ANNUEL",
                                "ANNUELLE",
                                "S",
                                "SEM",
                                "SEME",
                                "SEMESTRIEL",
                                "SEMESTRIELLE",
                                "M",
                                "MOIS",
                                "MENSUEL",
                                "MENSUELLE",
                                "T",
                                "TRIM",
                                "TRIMESTRIEL",
                                "TRIMESTRIELLE",
                            ) or vn in ("1", "2", "4", "12", "365", "180"):
                                # Fréquence coupon sur colonne ambiguë (ex. « A » annuel) : rester in fine.
                                cap_fin = True
                            else:
                                cap_fin = True

                    mt_ct = None
                    if col_maturite_ct and col_maturite_ct in row.index:
                        mv = _to_float_loose(row[col_maturite_ct])
                        if mv is not None and int(round(mv)) in (13, 26, 52):
                            mt_ct = int(round(mv))
                    if (
                        mt_ct is None
                        and str(row.get("TYPE_TAUX") or "").strip().upper() == "FIX"
                        and str(row.get("PERIODICITE_REMBOU") or "").strip().upper().startswith("FIN")
                        and str(row.get("BASE_CALCUL") or "").strip().upper() == "R/360"
                    ):
                        total_days_ct = int((dm - de).days)
                        if total_days_ct > 0 and total_days_ct <= 366:
                            if int(per) == 4:
                                mt_ct = 13
                            elif int(per) == 2:
                                mt_ct = 26
                            else:
                                mt_ct = 52
                    if (
                        mt_ct is None
                        and not is_rev_fin_atp
                        and str(row.get("CATEGORIE") or "").strip().upper() == "BDT"
                        and str(row.get("TYPE_TAUX") or "").strip().upper() == "FIX"
                        and str(row.get("PERIODICITE_REMBOU") or "").strip().upper().startswith("FIN")
                        and str(row.get("BASE_CALCUL") or "").strip().upper() == "R/360"
                        and str(row.get("GARANTIE") or "").strip().upper() == "O"
                        and (
                            str(row.get("PERIODICITE_COUPON") or "").strip().upper().startswith("SEM")
                            or str(row.get("PERIODICITE_COUPON") or "").strip().upper().startswith("AN")
                        )
                    ):
                        total_days_ct = int((dm - de).days)
                        if total_days_ct > 0 and total_days_ct <= 366:
                            mt_ct = min((13, 26, 52), key=lambda w: abs(total_days_ct - (w * 7)))
                    if mt_ct is None and is_rev_fin_atp:
                        peri_rev = str(row.get("PERIODICITE_COUPON") or "").strip().upper()
                        if peri_rev.startswith("TRI"):
                            mt_ct = 13
                        elif peri_rev.startswith("SEM"):
                            mt_ct = 26
                        elif peri_rev.startswith("AN"):
                            mt_ct = 52

                    # M vs A : règle Excel / WG (pas la colonne METHODE_VALO, sauf **L**).
                    mode_n_fichier = (
                        normaliser_mode_valo(str(row[col_mode]).strip())
                        if col_mode and col_mode in row.index
                        else ""
                    )
                    if mode_n_fichier == "L":
                        mode_raw = "L"
                    else:
                        j_si = (dm - dl).days
                        j_res = float(j_si) if j_si > 0 else float(mat_j)
                        mode_raw = mode_valorisation_atp_si_maturite_residuelle(j_res)
                    base_atp = 1
                    if col_base_atp and col_base_atp in row.index:
                        b_raw = _to_float_loose(row[col_base_atp])
                        if b_raw is not None and int(round(float(b_raw))) in (1, 2):
                            base_atp = int(round(float(b_raw)))
                    coupon_vba = False
                    if col_atp_coupon_vba and col_atp_coupon_vba in row.index:
                        raw_vba = row[col_atp_coupon_vba]
                        if raw_vba is not None and not (
                            isinstance(raw_vba, float) and np.isnan(float(raw_vba))
                        ):
                            sv = str(raw_vba).strip().upper()
                            coupon_vba = sv in (
                                "O",
                                "OUI",
                                "1",
                                "Y",
                                "YES",
                                "VRAI",
                                "TRUE",
                                "VBA",
                                "X",
                            ) or (isinstance(raw_vba, (int, float)) and float(raw_vba) == 1.0)
                    atp = prix_atp_dbt(
                        date_liquidation=dl,
                        date_emission=de,
                        date_jouissance=dj,
                        date_echeance=dm,
                        taux_coupon_annuel=float(tc),
                        nominal=float(nominal),
                        premier_j_inclus=pj,
                        mode_valorisation=mode_raw,
                        periodicite_cp=per,
                        periodicite_cap_fin=cap_fin,
                        rendement_annuel_effectif=rendement_brut,
                        maturite_semaines_ct=mt_ct,
                        actuariel_base=base_atp,
                        taux_coupon_comme_vba=coupon_vba,
                    )
                    ok_atp = (
                        not atp.get("amortissement_non_supporte")
                        and atp.get("flux_dates")
                        and math.isfinite(float(atp["prix_clean"]))
                    )
                    if ok_atp:
                        methode_coupon_norm = str(row.get("METHODE_COUPON") or "").strip().upper()
                        base_calcul_norm = str(row.get("BASE_CALCUL") or "").strip().upper()
                        type_taux_norm = str(row.get("TYPE_TAUX") or "").strip().upper()
                        periodicite_remb_norm = str(row.get("PERIODICITE_REMBOU") or "").strip().upper()
                        categorie_norm = str(row.get("CATEGORIE") or "").strip().upper()
                        if (
                            type_taux_norm == "REV"
                            and periodicite_remb_norm.startswith("FIN")
                            and methode_coupon_norm == "R/R"
                            and "R/360" in base_calcul_norm
                        ):
                            jours_coupon = max(0, int((dm - de).days))
                            jours_discount = max(0, int((dm - dl).days))
                            flux_direct = float(nominal) * (1.0 + float(tc) * jours_coupon / 365.0)
                            den_direct = 1.0 + float(rendement_brut) * jours_discount / 360.0
                            if den_direct > 0.0 and math.isfinite(den_direct):
                                atp["prix_clean"] = round(flux_direct / den_direct, 6)
                                atp["prix_dirty"] = float(atp["prix_clean"]) + float(atp.get("coupon_courru") or 0.0)
                                atp["flux_montants"] = [round(flux_direct, 2)]
                                atp["flux_dates"] = [dm]
                        elif (
                            type_taux_norm == "FIX"
                            and periodicite_remb_norm.startswith("FIN")
                            and methode_coupon_norm == "R/R"
                            and base_calcul_norm == "R/R"
                            and categorie_norm in ("CD", "BT")
                            and 366 < int((dm - de).days) <= 548
                            and int((dm - dl).days) <= 366
                        ):
                            jours_coupon = max(0, int((dm - de).days))
                            jours_discount = max(0, int((dm - dl).days))
                            flux_direct = float(nominal) * (1.0 + float(tc) * jours_coupon / 365.0)
                            den_direct = 1.0 + float(rendement_brut) * jours_discount / 360.0
                            if den_direct > 0.0 and math.isfinite(den_direct):
                                atp["prix_clean"] = round(flux_direct / den_direct, 6)
                                atp["prix_dirty"] = float(atp["prix_clean"]) + float(atp.get("coupon_courru") or 0.0)
                                atp["flux_montants"] = [round(flux_direct, 2)]
                                atp["flux_dates"] = [dm]
                        if (
                            type_taux_norm in ("FIX", "REV")
                            and str(row.get("METHODE_VALO") or "").strip().upper() == "AA"
                            and (
                                periodicite_remb_norm.startswith("FIN")
                                or (
                                    type_taux_norm == "REV"
                                    and (
                                        "R/360" in base_calcul_norm
                                        or "360" in methode_coupon_norm
                                    )
                                )
                            )
                            and isinstance(de, date)
                            and isinstance(dj, date)
                            and isinstance(dl, date)
                            and dl < _ajouter_mois_fin_mois(
                                dj,
                                3
                                if str(row.get("PERIODICITE_COUPON") or "").strip().upper().startswith("TRI")
                                else
                                6
                                if str(row.get("PERIODICITE_COUPON") or "").strip().upper().startswith("SEM")
                                else 12,
                            )
                        ):
                            denom_cc = 360.0 if "360" in base_calcul_norm else 365.0
                            cc_start = de if de <= dj else dj
                            jours_accrual = max(0, (dl - de).days)
                            atp["coupon_courru"] = round(
                                float(nominal) * float(tc) * max(0, (dl - cc_start).days) / denom_cc + 1e-12,
                                4,
                            )
                            atp["prix_dirty"] = float(atp["prix_clean"]) + float(atp.get("coupon_courru") or 0.0)

                        metric_day_base = 365.0
                        convexity_day_base = metric_day_base
                        metric_flux_dates = atp["flux_dates"]
                        metric_flux_montants = atp["flux_montants"]
                        metric_mode_atp = str(atp.get("mode_utilise") or "A")
                        metric_convexity_actuarial_first_flow = False
                        if (
                            type_taux_norm == "REV"
                            and (
                                (
                                    str(row.get("METHODE_VALO") or "").strip().upper() == "AA"
                                    and (
                                        "R/360" in base_calcul_norm
                                        or "360" in methode_coupon_norm
                                    )
                                )
                                or categorie_norm == "FPCT"
                                or str(row.get("S_CATEGORIE") or "").strip().upper() == "FPCTO"
                            )
                            and atp.get("flux_dates")
                            and atp.get("flux_montants")
                        ):
                            metric_flux_dates = [atp["flux_dates"][0]]
                            metric_flux_montants = [atp["flux_montants"][0]]
                            metric_mode_atp = "M"
                            metric_convexity_actuarial_first_flow = bool(
                                str(row.get("METHODE_VALO") or "").strip().upper() == "ZC"
                                and (
                                    categorie_norm == "FPCT"
                                    or str(row.get("S_CATEGORIE") or "").strip().upper() == "FPCTO"
                                )
                            )
                        m_atp = metriques_depuis_flux_atp(
                            dl,
                            metric_flux_dates,
                            metric_flux_montants,
                            float(atp["prix_clean"]),
                            rendement_brut,
                            premier_j_inclus=pj,
                            periodicite_cp=per,
                            mode_atp=metric_mode_atp,
                            actuariel_base=base_atp,
                            metric_day_base=metric_day_base,
                            convexity_day_base=convexity_day_base,
                            convexity_actuarial_first_flow=metric_convexity_actuarial_first_flow,
                        )
                        res = {
                            "prix_dirty": round(float(atp["prix_dirty"]), 6),
                            "prix_clean_atp": round(float(atp["prix_clean"]), 6),
                            "duration_macaulay": m_atp["duration_macaulay"],
                            "duration_modifiee": m_atp["duration_modifiee"],
                            "convexite": m_atp["convexite"],
                            "ytm": _arrondi_taux_decimal_excel(float(m_atp["ytm"]))
                            if math.isfinite(float(m_atp["ytm"]))
                            else float("nan"),
                            "coupon_courru_atp": round(float(atp["coupon_courru"]), 4),
                            "moteur_prix": "ATP",
                            "mode_atp": atp.get("mode_utilise"),
                            "taux_rendement_atp_utilise": rendement_affiche,
                            "actuariel_base": base_atp,
                        }
                        if _valo_trace_enabled():
                            flux_d = atp.get("flux_dates") or []
                            flux_m = [float(x) for x in (atp.get("flux_montants") or [])]
                            res["trace_flux_dates_iso"] = [d.isoformat() for d in flux_d]
                            res["trace_flux_montants"] = flux_m
                            res["trace_rendement_decimal"] = float(rendement_brut)
                            ji_tr = 1 if pj else 0
                            ytm_tr = float(rendement_brut)
                            dfs_list: list[float] = []
                            pvs_list: list[float] = []
                            for j in range(len(flux_d)):
                                di = (
                                    max(0.0, float((flux_d[j] - dl).days) - float(ji_tr))
                                    / float(metric_day_base)
                                )
                                df_ij = (
                                    float(np.power(1.0 + ytm_tr, -di))
                                    if ytm_tr > -1 and math.isfinite(di)
                                    else float("nan")
                                )
                                dfs_list.append(df_ij)
                                if j < len(flux_m):
                                    pvs_list.append(float(flux_m[j]) * df_ij)
                                else:
                                    pvs_list.append(float("nan"))
                            res["trace_discount_factors"] = dfs_list
                            res["trace_pv_flows"] = pvs_list
                            res["trace_somme_pv"] = (
                                float(np.nansum(np.asarray(pvs_list, dtype=float)))
                                if pvs_list
                                else float("nan")
                            )

            if not res:
                res = valoriser_ligne_obligation(
                    nominal,
                    float(tc),
                    float(mat_j),
                    courbe_zc_jours,
                    periodicite=per,
                    spread_decimal=spread_dec,
                    taux_secondaire_a_j=taux_secondaire_a_j,
                )
                res["coupon_courru_atp"] = 0.0
                res["moteur_prix"] = "ZC"
                if _valo_trace_enabled():
                    _pz, pay_d, cfs, rates = prix_obligation_courbe_zc(
                        nominal,
                        float(tc),
                        float(mat_j),
                        courbe_zc_jours,
                        periodicite=per,
                        spread_decimal=spread_dec,
                        taux_secondaire_a_j=taux_secondaire_a_j,
                    )
                    t_ann = pay_d / 365.0
                    dfs_z = np.power(1.0 + rates, -t_ann)
                    res["trace_flux_dates_iso"] = []
                    res["trace_flux_montants"] = []
                    res["trace_pay_days"] = [float(x) for x in pay_d]
                    res["trace_cash_flows"] = [float(x) for x in cfs]
                    res["trace_rates_actu_par_flux"] = [float(x) for x in rates]
                    res["trace_discount_factors"] = [float(x) for x in dfs_z]
                    res["trace_pv_flows"] = [
                        float(c) * float(d) for c, d in zip(cfs, dfs_z)
                    ]
                    res["trace_rendement_decimal"] = float("nan")
                    res["trace_somme_pv"] = float(np.sum(cfs * dfs_z))

            res["maturite_residuelle_jours"] = float(mat_j)
            res["taux_coupon_decimal"] = float(tc)
            res["nominal_valo"] = float(nominal)
            res["nominal_pricing"] = float(nominal)
            res["spread_decimal_valo"] = float(spread_dec)
            res["date_emission_iso"] = ""
            if col_date_emission and col_date_emission in row.index:
                dem = _parse_datetime_loose(row[col_date_emission])
                if dem is not None:
                    dde = dem.date() if hasattr(dem, "date") else dem
                    if hasattr(dde, "strftime"):
                        res["date_emission_iso"] = dde.strftime("%d/%m/%Y")
            base = {str(k): row[k] for k in df.columns}
            base.update(res)
            rows_out.append(base)
            if progress_label:
                moteur = str(res.get("moteur_prix") or "?")
                prix = res.get("prix_clean_atp", res.get("prix_clean", None))
                try:
                    prix_msg = f"{float(prix):.6f}" if prix is not None else "n/a"
                except (TypeError, ValueError):
                    prix_msg = "n/a"
                print(
                    f"[{progress_label}] {pos}/{progress_total} CODE {code_progress} termine | moteur={moteur} | prix={prix_msg}",
                    flush=True,
                )
        except Exception as exc:
            if progress_label:
                print(
                    f"[{progress_label}] {pos}/{progress_total} CODE {code_progress} erreur: {type(exc).__name__}: {exc}",
                    flush=True,
                )
            continue

    meta["nb_lignes_valorisees"] = len(rows_out)
    meta["nb_lignes_echeance_depassee"] = skipped_echeance_depassee
    if progress_label:
        print(
            f"[{progress_label}] fin valorisation: {len(rows_out)}/{progress_total} code(s) valorise(s)",
            flush=True,
        )
    return pd.DataFrame(rows_out), meta


# Alias pédagogique (corrigé par rapport au pseudo-code initial : taux en décimal, (1+r)^t)
def calcul_prix_obligation(
    nominal: float,
    taux_coup_decimal: float,
    maturite_annees: float,
    courbe_taux_ZC: list[tuple[float, float]],
    spread_decimal: float = 0.0,
) -> float:
    """
    Exemple : courbe_taux_ZC = [(365, 0.027), (730, 0.028), ...] en (jours, taux décimal).
    """
    courbe = {float(a[0]): float(a[1]) for a in courbe_taux_ZC}
    p, _, _, _ = prix_obligation_courbe_zc(
        nominal,
        taux_coup_decimal,
        float(maturite_annees) * 365.0,
        courbe,
        periodicite=1,
        spread_decimal=spread_decimal,
    )
    return p
