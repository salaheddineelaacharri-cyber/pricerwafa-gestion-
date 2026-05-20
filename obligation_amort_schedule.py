"""
Échéancier d’amortissement (type tableau Attijari / AWB) à partir du classeur base titre oblig.

Logique alignée sur une feuille type **Ammortissable** (réf. Excel) :
- **Capital restant** : si les amortissements **> 0** sont **tous égaux** sur **N** échéances (ex. 100 000 / 7),
  même règle qu’Excel **ARRONDI(nominal × (N − k) / N ; 2)** avec *k* = nb d’amortissements déjà
  passés jusqu’à la colonne (inclus) — évite les écarts de 0,01 vs une soustraction sur montants affichés.
  Si le **NOMINAL** référentiel ne colle pas à ``N × amort`` mais le fichier montre des amortissements **strictement
  constants**, on prend **N = nb de lignes > 0** et **nominal effectif = somme(amort fichier)** pour garder le même
  montant sur chaque tombée (évite un dernier terme résiduel type 6,34).
  Sinon : chaînage ``capital = capital_{i-1} − amortissement_i``.
- **Intérêts** : capital **début** de période × taux coupon (sauf ancrage / ``INTERET`` fichier). Si règle fraction
  ``N/k`` : l’encours utilisé est **nominal×(N−k_before)/N** (précision pleine), pas le capital restant **affiché** arrondi.
- **Flux** : amortissement + intérêts (sauf si ``FLUX`` explicite dans le fichier).
- **Flux actualisé** : ``flux / (1 + taux d'actualisation)^{durée}`` si tombée **strictement après** la valorisation ;
  sinon **0** (comme Excel) ; chaque cellule du tableau est arrondie **4 déc.** pour l’affichage.
  **Prix clean / Prix arrondi** = **Σ des PV en pleine précision** (avant arrondi cellule), aligné Manar / Excel sur le total ;
  ce n’est **pas** en général la somme des cellules « Flux actualisé » affichées (arrondies).
  **prix dirty** = cette somme + coupon couru.
- **Taux ZC** (affichage + actualisation) : si ``METHODE_VALO`` = **ZC**, interpolation sur l’échéancier annuel
  tracé (**TauxZCActuariel** vs **Maturity_days**, jours date tombée − valorisation) ; sinon colonne fichier titre ;
  sinon taux secondaire. Si **AA** / MN : ligne **Taux AA** = **taux secondaire interpolé** (Formule B, piliers CT/LT
  identiques au tableau « Comparaison interpolation BAM » côté API).
- **Flux restant** : ligne à **0** (convention tableau Excel AWB / Attijari).
- **durée** (ligne AWB) : rationnel jours/jours puis +1 entier en chaîne (voir ``construire_tableau_amortissement``) ;
  colonnes tombée ≤ valo : vide. Pas la duration Macaulay (pied de tableau).
- **Duration** (pied de tableau) : Macaulay / convexité via YTM implicite sur les flux futurs.

Feuilles attendues (noms flexibles, accents tolérés) :
- **Referentiel_titre** : ``CODE``, ``NOMINAL`` / ``VN``, ``VALEUR_TAUX`` / ``TAUX`` (coupon %),
  ``TYPE_TAUX`` (doit indiquer **REV**) : prix **révisable** —
  actualisation linéaire sur flux + capital à la prochaine date de révision ;
  **``METHODE_VALO``** : **AA** → secondaire Formule B (ligne « Taux AA », aligné comparaison BAM) ; **TA** →
  même **taux actuariel plat** que **AA** : secondaire interpolé à la **maturité résiduelle** (échéance − valo), pas de colonne YTM requise ;
  **ZC** → **TauxZCActuariel** de l’échéancier annuel (ligne « Taux ZC ») ; **MN** → même courbe que **AA** (secondaire),
  ``DESCRIPTION`` / libellé ; optionnel **``DATE_VALO``** / **``DATE_VALORISATION``** (équivalent cellule
  Excel **$C$1** pour la ligne durée REV) ; colonnes ``NOTE``, ``COMMENTAIRE``, type de valeur…
- **echeancier_Titre** : format **long**, une ligne par échéance. Identifiant titre : ``CODE`` **ou**
  ``TITRE`` (même sémantique que ``CODE`` du référentiel). Dates : ``DATE_REGLEMENT``, ``DATE_TOMBEE``,
  ``DATE_FIN``, etc. Principal : ``AMORTISSEMENT``, ``CAPITAL_AMORTIS``, … ; ``CAPITAL_RESTANT`` = encours
  début de période (Manar) : si la valorisation est après une tombée passée, le moteur ancre l’encours
  sur le ``CAPITAL_RESTANT`` SQL de la **première** ligne avec ``DATE_TOMBEE > date_valo`` puis enchaîne
  les flux futurs sur l’échéancier filtré IM.
  Colonnes optionnelles ``INTERET`` / ``COUPON``, ``FLUX``.

Les BDT (Bon du Trésor) avec **échéancier SQL** suivent la même grille que les autres titres
(ex. **201657** : alignement prix Manar vs ATP).
"""

from __future__ import annotations

import logging
import os

# Marqueur obligatoire dans ``PRICER_AMORT_ENGINE_ID`` : somme NPV pleine précision pour le prix clean
# (≠ Σ des cellules « Flux actualisé » arrondies à 4 déc.). ``backend.main`` refuse de démarrer si absent.
_NPV_HP_SUM_MARKER = "hpvsum"
PRICER_AMORT_ENGINE_ID = (
    "excel-amm-h478-h47910-h482dec5-metvalo-zcpow-fper-trireel-fixaa-spreadrr-taa5d-fixuni-"
    f"zcactj-r12-s6off-r5hu-mon1y-rembou365-cptri-{_NPV_HP_SUM_MARKER}-imscope-2026-05-14"
)
ATP_SCHEDULE_REALIGN_CODES = {
    "153159",
    "9596",
    "9529",
    "9428",
    "9389",
    "9363",
    "2185",
    "9752",
}

def _table_amort_doit_aligner_prix(tab: dict[str, Any]) -> bool:
    """Indique si l'échéancier doit remplacer le prix ATP/titre dans Valorisation."""
    code_s = _normaliser_code(tab.get("code"))
    if code_s in ATP_SCHEDULE_REALIGN_CODES:
        return False
    if str(tab.get("categorie") or "").strip().upper() == "FPCT":
        return True
    if bool(tab.get("is_amortissable")):
        return True
    # Les REV/ZC in fine (et FPCT) suivent l'échéancier. REV/AA in fine : même NPV
    # échéancier que Manar (prochain flux linéaire / ZC) — ne pas laisser l'ATP écraser.
    if bool(tab.get("pricing_rev_bond")) and bool(tab.get("courbe_zc_active")):
        return True
    pr = str(tab.get("periodicite_rembou") or "").strip().upper()
    if bool(tab.get("pricing_rev_bond")) and not bool(tab.get("courbe_zc_active")) and pr in ("FIN", "F"):
        return True
    # FIX/AA in fine (BSF type 100954) : NPV grille = référence Manar, pas le prix ATP seul.
    if (
        bool(tab.get("pricing_fix_bond"))
        and not bool(tab.get("courbe_zc_active"))
        and pr in ("FIN", "F")
    ):
        return True
    return False

import math
import sys
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from pricing_atp import _normalise_taux_coupon_annuel_wg_deux_dec_pct
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from pricing.data_access import (
    charger_referentiel_et_echeancier as charger_referentiel_et_echeancier_sql,
    charger_referentiel_et_echeancier_codes as charger_referentiel_et_echeancier_codes_sql,
    diagnostic_sources_sql,
)
from dateutil.relativedelta import relativedelta

from yield_curve import convexity as yc_convexity
from valuation_zc_obligations import normaliser_spread_emission, spread_decimal_arrondi_prime_pct3

_ROOT_PROJ = Path(__file__).resolve().parent
if str(_ROOT_PROJ) not in sys.path:
    sys.path.insert(0, str(_ROOT_PROJ))

try:
    from backend.app.services.bond_pricing import (
        calculer_duree_affichage_rev,
        calculate_rev_bond_price,
        prix_rev_actualise_excel_puissance,
        prix_rev_lineaire_act360,
        taux_actualisation_rev_arrondi_excel,
    )
except ImportError:

    def calculer_duree_affichage_rev(
        date_valorisation: date,
        date_tombee: date,
        periodicite_coupon: str | None,
        base_calcul: str | None,
        *,
        code: str | int | None = None,
    ) -> float:
        _ = code
        if date_valorisation >= date_tombee:
            return 0.0
        j = (date_tombee - date_valorisation).days
        peri = str(periodicite_coupon or "").strip().upper()
        base = str(base_calcul or "").strip().upper()
        use_r360 = "R/360" in base
        if "TRI" in peri:
            f, d = 0.25, 91.0
        elif "SEM" in peri:
            f, d = 0.50, (180.0 if use_r360 else 182.0)
        else:
            f, d = 1.0, (360.0 if use_r360 else 365.0)
        return round((j / d) * f, 10)

    def calculate_rev_bond_price(
        date_valorisation: date,
        df_echeancier,
        *,
        flux_prochain: float,
        capital_restant: float,
        taux_actualisation_decimal: float,
        date_column: str = "DATE_REGLEMENT",
        code: str | int | None = None,
        code_column: str = "CODE",
    ) -> tuple[float, int, float, date]:
        rows = df_echeancier.to_dict("records") if hasattr(df_echeancier, "to_dict") else list(df_echeancier or [])
        code_s = str(code).strip() if code is not None else None
        d_futures: list[date] = []
        for r in rows:
            if code_s is not None and str(r.get(code_column, "")).strip() != code_s:
                continue
            d = _parse_date_cell(r.get(date_column))
            if d is not None and d > date_valorisation:
                d_futures.append(d)
        if not d_futures:
            raise ValueError("Aucune date de révision future trouvée dans l'échéancier.")
        next_revision_date = min(d_futures)
        jours = (next_revision_date - date_valorisation).days
        duree = jours / 360.0
        prix = prix_rev_lineaire_act360(
            flux_prochain=flux_prochain,
            capital_restant_apres=capital_restant,
            taux_actualisation_decimal=taux_actualisation_decimal,
            jours_act360=jours,
        )
        return prix, jours, duree, next_revision_date

    def prix_rev_lineaire_act360(
        flux_prochain: float,
        capital_restant_apres: float,
        taux_actualisation_decimal: float,
        jours_act360: int,
    ) -> float:
        t = max(0, int(jours_act360)) / 360.0
        num = float(flux_prochain) + float(capital_restant_apres)
        r = float(taux_actualisation_decimal)
        den = 1.0 + r * t
        if den <= 0.0 or not math.isfinite(den):
            return 0.0
        # Excel feuille REV: ARRONDI((Flux+Capital)/(1+TauxActu*Durée); 5)
        return round(num / den + 1e-12, 5)

    def taux_actualisation_rev_arrondi_excel(
        taux_aa_decimal: float,
        spread_decimal: float,
        ndigits_pct: int = 5,
    ) -> tuple[float, float]:
        aa = float(taux_aa_decimal)
        sp = float(spread_decimal)
        pct = round((aa + sp) * 100.0 + 1e-15, ndigits_pct)
        dec = pct / 100.0
        if not math.isfinite(dec):
            return 0.0, 0.0
        return float(pct), float(dec)

    def prix_rev_actualise_excel_puissance(
        flux_plus_capital: float,
        taux_actualisation_decimal: float,
        duree_exposant: float,
    ) -> float:
        fv = float(flux_plus_capital)
        r = float(taux_actualisation_decimal)
        t = float(duree_exposant)
        if fv <= 0.0 or not math.isfinite(fv):
            return 0.0
        if t <= 0.0:
            return round(fv + 1e-12, 5)
        base = 1.0 + r
        if base <= 0.0 or not math.isfinite(base):
            return 0.0
        den = base**t
        if den <= 0.0 or not math.isfinite(den):
            return 0.0
        return round(fv / den + 1e-12, 5)


def _norm_txt(s: str) -> str:
    return (
        str(s)
        .strip()
        .lower()
        .replace("é", "e")
        .replace("è", "e")
        .replace("ê", "e")
        .replace("à", "a")
        .replace("ô", "o")
        .replace("_", " ")
    )


def _decimal_taux_courbe_fix_aa_pour_actu(r_decimal: float) -> float:
    """
    Taux **décimal** de la courbe secondaire (FIX / AA) : **ARRONDI à 5 décimales** (demi pair / type Excel
    ``ARRONDI(taux;5)`` sur le décimal), pas une troncature vers 0.

    Sans cela, un interpolé du type ``0,026315778…`` (piliers BAM LT 326→643 j) tombait en **2,631 %** après
    troncature alors que Manar affiche **2,632 %**.

    Un **pré-arrondi** (12 déc.) sur le flottant évite les biais IEEE avant le quantize ``1e-5``.
    """
    r = float(r_decimal)
    if not math.isfinite(r):
        return 0.0
    r = round(r, 12)
    return float(Decimal(str(r)).quantize(Decimal("1e-5"), rounding=ROUND_HALF_UP))


def _pct_taux_courbe_fix_aa_display(r_decimal: float) -> float:
    """
    Affichage « Taux AA » (FIX, courbe secondaire) : même base décimale que
    ``_decimal_taux_courbe_fix_aa_pour_actu``, puis pourcentage sur **3** décimales.
    """
    r5 = _decimal_taux_courbe_fix_aa_pour_actu(r_decimal)
    return float(Decimal(str(r5 * 100.0)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def _round_excel(value: float, ndigits: int) -> float:
    """Equivalent Excel ARRONDI(...; ndigits) avec demi vers le haut."""
    if not math.isfinite(float(value)):
        return 0.0
    q = Decimal("1").scaleb(-int(ndigits))
    return float(Decimal(str(float(value))).quantize(q, rounding=ROUND_HALF_UP))


def _trouver_feuille(noms: list[str], fragments: tuple[str, ...]) -> str | None:
    for n in noms:
        k = _norm_txt(n)
        if all(f in k for f in fragments):
            return n
    return None


def _trouver_feuille_referentiel(noms: list[str]) -> str | None:
    sh = _trouver_feuille(noms, ("referentiel", "titre"))
    if sh:
        return sh
    for n in noms:
        k = _norm_txt(n)
        if "referentiel" in k:
            return n
    return None


def _trouver_feuille_echeancier(noms: list[str]) -> str | None:
    """Feuille des tombées : noms usuels + toute feuille dont le nom contient *echeancier* (hors référentiel)."""
    for frags in (
        ("echeancier", "titre"),
        ("echeancier", "obligation"),
    ):
        hit = _trouver_feuille(noms, frags)
        if hit:
            return hit
    for n in noms:
        k = _norm_txt(n)
        if "referentiel" in k:
            continue
        if "echeancier" in k:
            return n
        if "echeancer" in k:
            return n
        if "amortis" in k and ("tableau" in k or "echean" in k):
            return n
    return None


def diagnostic_feuilles_amortissement(path: Path) -> dict[str, Any]:
    """Aide au debug UI : sources SQL chargees pour l'amortissement."""
    return diagnostic_sources_sql()


def _normaliser_entete_feuille_excel(name: str) -> str:
    """En-tête colonne Excel : BOM, espaces insécables, espaces de tête/queue."""
    s = str(name).strip().strip("\ufeff")
    s = s.replace("\xa0", " ").replace("\u202f", " ")
    return s.strip()


def charger_referentiel_et_echeancier(
    path: Path,
    codes: list[str] | tuple[str, ...] | set[str] | None = None,
) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """Charge ``Referentiel_titre`` et ``echeancier_Titre`` depuis SQL Server."""
    try:
        if codes:
            ref, ech = charger_referentiel_et_echeancier_codes_sql(codes)
        else:
            ref, ech = charger_referentiel_et_echeancier_sql()
    except Exception:
        return None, None
    ref.columns = [_normaliser_entete_feuille_excel(str(c)) for c in ref.columns]
    ech.columns = [_normaliser_entete_feuille_excel(str(c)) for c in ech.columns]
    return ref, ech


def _normaliser_code(v: Any) -> str:
    if v is None or (isinstance(v, float) and (math.isnan(v) or np.isnan(v))):
        return ""
    s = str(v).strip()
    if re.fullmatch(r"\d+\.0", s):
        s = s[:-2]
    return s


def _serie_code(df: pd.DataFrame, col: str) -> pd.Series:
    return df[col].map(_normaliser_code)


def _detecter_colonne_code(df: pd.DataFrame) -> str | None:
    for c in df.columns:
        u = str(c).strip().upper().replace("É", "E")
        if u == "CODE":
            return str(c)
    for c in df.columns:
        u = str(c).strip().upper().replace("É", "E")
        if u == "CODE ET" or "CODE ET" in u:
            return str(c)
    for c in df.columns:
        u = str(c).strip().upper().replace("É", "E")
        if "CODE MAROCLEAR" in u or (u.startswith("CODE ") and u != "CODE ET"):
            return str(c)
    for c in df.columns:
        k = _norm_txt(c)
        if k == "code" or k.endswith(" code"):
            return str(c)
    # Base titre OBLG : feuille echeancier_Titre utilise souvent TITRE = même clé que CODE (Referentiel_titre).
    for c in df.columns:
        u = str(c).strip().upper().replace("É", "E")
        if u == "TITRE":
            return str(c)
    return None


def _detecter_colonne_date_debut(df: pd.DataFrame) -> str | None:
    for c in df.columns:
        k = _norm_txt(str(c))
        if "date" in k and "debut" in k:
            return str(c)
        if "date" in k and "début" in str(c).lower():
            return str(c)
    return None


def _detecter_colonne_num_evenement(df: pd.DataFrame) -> str | None:
    for c in df.columns:
        k = _norm_txt(str(c))
        if "num" in k and "event" in k:
            return str(c)
    return None


def _detecter_colonne_date(df: pd.DataFrame) -> str | None:
    dated: list[tuple[str, str]] = []
    for c in df.columns:
        k = _norm_txt(c)
        if "date" in k and "emis" not in k:
            dated.append((str(c), k))
    if not dated:
        return None

    def _pick(pred: Callable[[str], bool]) -> str | None:
        for col, k in dated:
            if pred(k):
                return col
        return None

    for pred in (
        lambda k: "tombe" in k,
        lambda k: "echeance" in k,
        lambda k: "paiement" in k,
        lambda k: "reglement" in k,
        lambda k: " fin" in f" {k}" or k.endswith(" fin"),
        lambda k: "debut" in k or "début" in k,
    ):
        hit = _pick(pred)
        if hit:
            return hit
    return dated[0][0]


def _detecter_colonne_amortissement(df: pd.DataFrame) -> str | None:
    for c in df.columns:
        k = _norm_txt(c)
        if "capital" in k and "amortis" in k:
            return str(c)
        if "montant" in k and "amort" in k:
            return str(c)
        if any(x in k for x in ("amort", "amortis")):
            return str(c)
        if "principal" in k and "rembours" in k:
            return str(c)
        if k.startswith("capital") and "rembours" in k:
            return str(c)
    return None


def _detecter_colonne_capital_restant(df: pd.DataFrame) -> str | None:
    """Encours début de période tel que stocké en base (ex. ``CAPITAL_RESTANT`` SQL)."""
    for c in df.columns:
        u = str(c).strip().upper().replace("É", "E")
        if u == "CAPITAL_RESTANT":
            return str(c)
    for c in df.columns:
        k = _norm_txt(c)
        if "capital" in k and "restant" in k and "amortis" not in k:
            return str(c)
    return None


def _detecter_colonne_interet(df: pd.DataFrame) -> str | None:
    for c in df.columns:
        k = _norm_txt(c)
        if "interet" in k or "intérêt" in str(c).lower():
            return str(c)
        if "coupon" in k and "couru" not in k:
            return str(c)
    return None


def _detecter_colonne_flux(df: pd.DataFrame) -> str | None:
    for c in df.columns:
        k = _norm_txt(c)
        if k == "flux" or k.startswith("flux "):
            return str(c)
    return None


def _detecter_colonne_taux_zc_schedule(df: pd.DataFrame) -> str | None:
    """Colonne taux ZC figé dans l’échéancier (ex. 2,328 % partout comme Excel)."""
    for c in df.columns:
        k = _norm_txt(str(c))
        if "actualis" in k or "prime" in k or "spread" in k:
            continue
        if "taux" in k and "zc" in k:
            return str(c)
        if k in ("zc", "tauxzc") or k.startswith("taux zc"):
            return str(c)
    return None


def _taux_decimal_depuis_cellule_excel(v: float) -> float:
    """Valeur saisie en % (ex. 2.328) ou en décimal (ex. 0.02328)."""
    fv = float(v)
    if abs(fv) <= 1.0:
        return fv
    return fv / 100.0


def _taux_zc_depuis_bloc_echeancier(
    ech: pd.DataFrame,
    code: str,
    *,
    d_valo_ech: date | None = None,
) -> float | None:
    """Taux ZC figé dans l’échéancier : même sous-ensemble que le tableau (filtre IM si applicable)."""
    if d_valo_ech is not None:
        sub, _dbg = _subset_echeancier_code_avec_filtre_im(ech, code, d_valo_ech)
    else:
        c_code = _detecter_colonne_code(ech)
        if not c_code:
            return None
        sub = ech[_serie_code(ech, c_code) == _normaliser_code(code)]
    c_tzc = _detecter_colonne_taux_zc_schedule(sub)
    if not c_tzc or c_tzc not in sub.columns or sub.empty:
        return None
    for _, row in sub.iterrows():
        x = _parse_float(row[c_tzc])
        if x is not None:
            return _taux_decimal_depuis_cellule_excel(float(x))
    return None


def _parse_date_cell(v: Any) -> date | None:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        s = v.strip()
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    try:
        ts = pd.to_datetime(v, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.date()
    except Exception:
        return None


def _colonnes_version_im(df: pd.DataFrame) -> tuple[str | None, str | None]:
    up = {str(c).upper().replace("É", "E"): str(c) for c in df.columns}
    c_ini = up.get("IM_DATE_INI")
    c_end = up.get("IM_DATE")
    if c_ini and c_ini in df.columns and c_end and c_end in df.columns:
        return c_ini, c_end
    return None, None


def _filtrer_echeancier_version_im(df: pd.DataFrame, d_valo: date) -> pd.DataFrame:
    """Version active : ``IM_DATE_INI <= d_valo`` et ``IM_DATE > d_valo`` (strict à droite)."""
    c_ini, c_end = _colonnes_version_im(df)
    if not c_ini or not c_end:
        return df
    di = df[c_ini].map(_parse_date_cell)
    de = df[c_end].map(_parse_date_cell)
    has_both = di.notna() & de.notna()
    if not bool(has_both.any()):
        # Aucune date IM exploitable : ne pas inventer un filtre (feuilles sans IM).
        return df
    m = has_both & (di <= d_valo) & (de > d_valo)
    if not bool(m.any()):
        # Fenêtres IM présentes mais aucune version à la date de valorisation : ne pas
        # retomber sur l’union de toutes les versions (ex. ~327 lignes mélangées).
        return df.iloc[0:0].copy()
    return df.loc[m].copy()


def _distinct_dates_im_series(col: pd.Series) -> list[str]:
    seen: set[date] = set()
    for v in col:
        d = _parse_date_cell(v)
        if d is not None:
            seen.add(d)
    return [str(x) for x in sorted(seen)]


def _premier_flux_futur_apres_valo(sub_v: pd.DataFrame, d_valo: date) -> tuple[date | None, Any]:
    """Première ligne (au sens date d’échéance / tombée) strictement après ``d_valo``."""
    if sub_v.empty:
        return None, None
    c_date = _detecter_colonne_date(sub_v)
    if not c_date:
        return None, None
    up = {str(c).upper().replace("É", "E"): str(c) for c in sub_v.columns}
    c_cb = up.get("COUPON_BRUT")
    best_d: date | None = None
    best_coupon: Any = None
    for _, row in sub_v.iterrows():
        d = _parse_date_cell(row[c_date])
        if d is None or d <= d_valo:
            continue
        if best_d is None or d < best_d:
            best_d = d
            best_coupon = row[c_cb] if c_cb and c_cb in sub_v.columns else None
    return best_d, best_coupon


def _debug_ech_im_snapshot(
    sub_avant: pd.DataFrame,
    sub_apres: pd.DataFrame,
    d_valo: date,
    *,
    filtre_im_actif: bool,
) -> dict[str, Any]:
    c_ini, c_end = _colonnes_version_im(sub_avant)
    ini_d: list[str] = []
    end_d: list[str] = []
    if (
        not sub_apres.empty
        and c_ini
        and c_end
        and c_ini in sub_apres.columns
        and c_end in sub_apres.columns
    ):
        ini_d = _distinct_dates_im_series(sub_apres[c_ini])
        end_d = _distinct_dates_im_series(sub_apres[c_end])
    fut_d, fut_cb = _premier_flux_futur_apres_valo(sub_apres, d_valo)
    return {
        "nb_lignes_avant_filtre": len(sub_avant),
        "nb_lignes_apres_filtre": len(sub_apres),
        "im_date_ini_distinct": ini_d,
        "im_date_distinct": end_d,
        "premier_flux_date_apres_valo": str(fut_d) if fut_d else None,
        "premier_flux_coupon_brut": fut_cb,
        "filtre_im_actif": filtre_im_actif,
    }


def _subset_echeancier_code_avec_filtre_im(
    ech: pd.DataFrame, code: str, d_valo_ech: date
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Sous-ensemble du titre puis filtre IM ; métadonnées pour trace ``[DEBUG_ECH_USED]``."""
    empty_meta: dict[str, Any] = {
        "nb_lignes_avant_filtre": 0,
        "nb_lignes_apres_filtre": 0,
        "im_date_ini_distinct": [],
        "im_date_distinct": [],
        "premier_flux_date_apres_valo": None,
        "premier_flux_coupon_brut": None,
        "filtre_im_actif": False,
    }
    c_code = _detecter_colonne_code(ech)
    if not c_code:
        return ech.iloc[0:0].copy(), empty_meta
    sub = ech[_serie_code(ech, c_code) == _normaliser_code(code)].copy()
    c_ini, c_end = _colonnes_version_im(ech)
    im_cols = bool(
        c_ini and c_end and c_ini in sub.columns and c_end in sub.columns
    )
    if sub.empty:
        return sub, _debug_ech_im_snapshot(sub, sub, d_valo_ech, filtre_im_actif=False)
    if not im_cols:
        return sub, _debug_ech_im_snapshot(sub, sub, d_valo_ech, filtre_im_actif=False)
    sub_v = _filtrer_echeancier_version_im(sub, d_valo_ech)
    meta = _debug_ech_im_snapshot(sub, sub_v, d_valo_ech, filtre_im_actif=True)
    return sub_v, meta


def _parse_float(v: Any) -> float | None:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    try:
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return None
        return x
    except (TypeError, ValueError):
        return None


def _nb_decimales_fractionnaires_cellule_pct(raw: Any) -> int | None:
    if raw is None:
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


def _arrondi_taux_ref_si_3_decimales(raw_cell: Any, taux_decimal: float) -> float:
    t = float(taux_decimal)
    if not math.isfinite(t):
        return t
    if abs(t) > 1.0:
        t = t / 100.0
    nd = _nb_decimales_fractionnaires_cellule_pct(raw_cell)
    if nd is not None and nd >= 3:
        pct = t * 100.0
        pct2 = float(Decimal(str(pct)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        return pct2 / 100.0
    if nd is None:
        pct = t * 100.0
        pct2 = float(Decimal(str(pct)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        if abs(pct - pct2) > 1e-6:
            return pct2 / 100.0
    return t


def _ligne_referentiel(ref: pd.DataFrame, code: str) -> pd.Series | None:
    col = _detecter_colonne_code(ref)
    if not col:
        return None
    m = _serie_code(ref, col) == _normaliser_code(code)
    if not m.any():
        return None
    return ref.loc[m].iloc[0]


def _cellule_texte_excel_normalisee(raw: Any) -> str:
    """
    Valeur lue depuis Excel : ``strip``, espaces insécables, caractères de largeur nulle,
    apostrophe de **texte forcé** en tête (``'REV`` → ``REV``).
    """
    if raw is None:
        return ""
    if isinstance(raw, float) and (math.isnan(raw) or np.isnan(raw)):
        return ""
    try:
        if pd.isna(raw):
            return ""
    except (TypeError, ValueError):
        pass
    s = str(raw).strip().strip("\ufeff")
    s = s.replace("\xa0", " ").replace("\u202f", " ")
    for z in ("\u200b", "\u200c", "\u200d"):
        s = s.replace(z, "")
    s = s.strip()
    if len(s) >= 1 and s[0] in ("'", "\u2018", "\u2019"):
        s = s[1:].strip()
    return s


def _est_colonne_type_taux(nom_colonne: str) -> bool:
    """Reconnaît ``TYPE_TAUX``, ``TYPE TAUX``, ``Type de taux``, etc."""
    u = _normaliser_entete_feuille_excel(nom_colonne).upper().replace("É", "E")
    compact = re.sub(r"[\s_\-]+", "", u)
    if compact == "TYPETAUX":
        return True
    if u.startswith("TYPE") and "TAUX" in u.replace(" ", ""):
        if "VALEUR" in compact or "COURS" in compact:
            return False
        return len(compact) < 36
    return False


def _valeur_type_taux_indique_rev(raw: Any) -> bool:
    v = _cellule_texte_excel_normalisee(raw).upper().replace("É", "E")
    if not v or v in ("NAN", "NONE", "-", "#N/A"):
        return False
    return "REV" in v


def _libelle_indique_rev_semestriel(libelle: str) -> bool:
    """
    Détecte un titre **révisable** dans le libellé (ex. « … REV 26 SEM 4 ANS »).

    On exige le mot entier **REV** (évite des faux positifs type « PREVISION » si le texte
    est un jour normalisé différemment). Les classeurs n’ont pas toujours ``TYPE_TAUX`` rempli.
    """
    s = (libelle or "").upper().replace("É", "E")
    if not s or s in ("NAN", "NONE", "-"):
        return False
    return bool(re.search(r"(^|[^A-Z0-9])REV([^A-Z0-9]|$)", s))


def _type_taux_est_rev(ref_row: pd.Series | None, description_titre: str = "") -> bool:
    """True si obligation révisable : uniquement via colonne ``TYPE_TAUX`` = REV."""
    _ = description_titre
    if ref_row is not None:
        for c in ref_row.index:
            if _est_colonne_type_taux(str(c)) and _valeur_type_taux_indique_rev(ref_row[c]):
                return True
            k = _norm_txt(str(c))
            if "type" in k and "taux" in k and not _est_colonne_type_taux(str(c)):
                if _valeur_type_taux_indique_rev(ref_row[c]):
                    return True
    return False


def _type_taux_est_fix(ref_row: pd.Series | None) -> bool:
    """True si ``TYPE_TAUX`` indique explicitement FIX (sans impacter REV)."""
    if ref_row is None:
        return False
    for c in ref_row.index:
        if not _est_colonne_type_taux(str(c)):
            continue
        v = _cellule_texte_excel_normalisee(ref_row[c]).upper().replace("É", "E")
        if not v or v in ("NAN", "NONE", "-", "#N/A"):
            continue
        if "REV" in v:
            return False
        if "FIX" in v:
            return True
    return False


def _jour_mois_echeance_titre(
    lignes_echeancier: list[dict[str, Any]],
    ref_row: pd.Series | None,
) -> tuple[int, int] | None:
    """(mois, jour) de la dernière tombée (souvent aligné sur la date légale d’échéance)."""
    if lignes_echeancier:
        d_last = lignes_echeancier[-1]["date"]
        if isinstance(d_last, date):
            return d_last.month, d_last.day
    if ref_row is None:
        return None
    for c in ref_row.index:
        k = _norm_txt(str(c))
        if "date" in k and "echeance" in k:
            d = _parse_date_cell(ref_row[c])
            if d is not None:
                return d.month, d.day
    return None


def _indice_prochaine_date_revision(
    cols_dates: list[date],
    d_valo: date,
    mois_jour: tuple[int, int] | None,
) -> int | None:
    """
    Prochaine date de **révision** strictement après la valorisation.

    Si ``mois_jour`` est connu (échéance finale), on retient la première tombée ``d > d_valo``
    avec ce (mois, jour) — ex. titres semestriels avec révision annuelle le 4 juin.
    Sinon : première tombée après valo avec amortissement > 0.
    """
    if mois_jour is not None:
        m0, j0 = mois_jour
        for i, d in enumerate(cols_dates):
            if d > d_valo and d.month == m0 and d.day == j0:
                return i
    for i, d in enumerate(cols_dates):
        if d <= d_valo:
            continue
        return i
    return None


def obligation_est_bdt(description: str, ref_row: pd.Series | None) -> bool:
    d = (description or "").upper()
    if "BDT" in d:
        return True
    if "BON DU TRESOR" in d or "BON DU TRÉSOR" in (description or "").upper():
        return True
    if "BON DU TRESOR" in d.replace("É", "E"):
        return True
    if ref_row is None:
        return False
    for c in ref_row.index:
        k = _norm_txt(c)
        if any(x in k for x in ("type", "libell", "categorie", "famille", "nature")):
            v = str(ref_row[c]).upper()
            if "BDT" in v or "TRESOR" in v.replace("É", "E") or "TRÉSOR" in str(ref_row[c]).upper():
                return True
    return False


def _taux_coupon_depuis_ref(ref_row: pd.Series | None, fallback: float) -> float:
    def _snap_dec(t: float) -> float:
        t = _arrondi_taux_ref_si_3_decimales(None, float(t))
        if math.isfinite(t) and abs(t) <= 1.0:
            return float(_normalise_taux_coupon_annuel_wg_deux_dec_pct(t))
        return t

    if ref_row is None:
        return _snap_dec(fallback)
    for c in ref_row.index:
        k = _norm_txt(c)
        if k in ("valeur_taux", "taux facial", "taux", "coupon"):
            x = _parse_float(ref_row[c])
            if x is not None:
                x = _arrondi_taux_ref_si_3_decimales(ref_row[c], float(x))
                return _snap_dec(float(x))
    return _snap_dec(fallback)


def _nominal_depuis_ref(ref_row: pd.Series | None, fallback: float) -> float:
    if ref_row is None:
        return fallback
    for c in ref_row.index:
        k = _norm_txt(c)
        if k in ("nominal", "vn", "vm", "encours"):
            x = _parse_float(ref_row[c])
            if x is not None and x > 0:
                return float(x)
    return fallback


def _spread_depuis_ref(ref_row: pd.Series | None, fallback: float) -> float:
    if ref_row is None:
        return float(fallback)
    for c in ref_row.index:
        k = _norm_txt(c)
        compact = re.sub(r"[\s_\-]+", "", k)
        if compact.startswith("type") or "methode" in k:
            continue
        if compact in ("spreademission", "primeemission") or (
            "spread" in k and "emission" in k
        ):
            try:
                return float(spread_decimal_arrondi_prime_pct3(float(normaliser_spread_emission(ref_row[c]))))
            except Exception:
                return float(fallback)
    return float(fallback)


def _prime_pct_excel_rev_aa(spread_decimal: float) -> float:
    """
    Prime affichée/utilisée pour REV + AA.

    Cas métier observé : un spread source de ``106.5`` bp doit afficher ``1.066 %`` dans Excel,
    alors qu'un arrondi décimal strict de ``1.065`` resterait ``1.065``.
    On reproduit ce tie-break en majorant de 0.001 % les cas demi-bp.
    """
    pct = float(spread_decimal) * 100.0
    q = float(Decimal(str(pct)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))
    bp = float(spread_decimal) * 10000.0
    frac_bp = abs(bp - math.trunc(bp))
    if abs(frac_bp - 0.5) < 1e-9:
        q += 0.001 if pct >= 0 else -0.001
    return q


def _mois_par_periodicite_coupon(periodicite_coupon: str | None) -> int:
    peri = str(periodicite_coupon or "").strip().upper()
    if "TRI" in peri:
        return 3
    if "SEM" in peri:
        return 6
    return 12


def _step_fraction_from_periodicite(periodicite: str) -> Fraction:
    peri = str(periodicite or "").strip().upper()
    if peri.startswith("SEM"):
        return Fraction(1, 2)
    if peri.startswith("TRI"):
        return Fraction(1, 4)
    if peri.startswith("MEN"):
        return Fraction(1, 12)
    return Fraction(1, 1)


def _date_coupon_precedent_rr(date_tombee: date, periodicite_coupon: str | None) -> date:
    mois = _mois_par_periodicite_coupon(periodicite_coupon)
    if relativedelta is not None:
        return date_tombee - relativedelta(months=mois)
    year = date_tombee.year
    month = date_tombee.month - mois
    while month <= 0:
        month += 12
        year -= 1
    day = date_tombee.day
    while day > 0:
        try:
            return date(year, month, day)
        except ValueError:
            day -= 1
    return date(year, month, 1)


def _note_depuis_ref(ref_row: pd.Series | None) -> str | None:
    if ref_row is None:
        return None
    for c in ref_row.index:
        k = _norm_txt(c)
        if any(x in k for x in ("note", "commentaire", "remarque", "observation")):
            v = ref_row[c]
            if v is not None and str(v).strip() and str(v).lower() != "nan":
                return str(v).strip()
    return None


def _description_depuis_ref(ref_row: pd.Series | None) -> str:
    if ref_row is None:
        return ""
    for c in ref_row.index:
        k = _norm_txt(c)
        if any(x in k for x in ("description", "libell", "nom valeur", "nom_valeur")):
            v = ref_row[c]
            if v is not None and str(v).strip():
                return str(v).strip()
    return ""


def _date_valorisation_oblig_depuis_ref(ref_row: pd.Series | None, d_global: date) -> date:
    """
    Date de valorisation **par titre** (équivalent Excel **$C$1** sur la feuille REV).

    Colonnes reconnues sur ``Referentiel_titre`` (noms normalisés) : ``DATE_VALO``,
    ``DATE_VALORISATION``, ``DATE_LIQUIDATION``, ``DT_VALO``, etc. — jamais échéance / émission.
    Si absent : ``d_global`` (date envoyée par l’API / l’UI).
    """
    if ref_row is None:
        return d_global
    for c in ref_row.index:
        u = _normaliser_entete_feuille_excel(str(c)).upper().replace("É", "E")
        compact = re.sub(r"[\s_\-]+", "", u)
        if "ECHEANCE" in compact or "EMISSION" in compact or "ECHEAN" in compact:
            continue
        if compact in (
            "DATEVALO",
            "DATEVALORISATION",
            "DTVALO",
            "DATEDELIQUIDATION",
            "DATELIQUIDATION",
            "DATEVALORISATIONTITRE",
        ) or (
            "VALO" in compact
            and "DATE" in compact
            and "SPREAD" not in compact
            and "TAUX" not in compact
        ):
            d = _parse_date_cell(ref_row[c])
            if d is not None and 1990 <= d.year <= 2100:
                return d
    return d_global


def _date_emission_depuis_ref(ref_row: pd.Series | None) -> date | None:
    if ref_row is None:
        return None
    # Priorité 1 : colonne dont le nom contient "date" ET "emission"
    for c in ref_row.index:
        k = _norm_txt(str(c))
        if "date" in k and "emission" in k:
            d = _parse_date_cell(ref_row[c])
            if d is not None and d.year >= 1990:
                return d
    # Priorité 2 : colonne "emission" seule (exclure SPREAD_EMISSION, etc.)
    for c in ref_row.index:
        k = _norm_txt(str(c))
        if "emission" in k and "echeance" not in k and "spread" not in k and "prime" not in k:
            d = _parse_date_cell(ref_row[c])
            if d is not None and d.year >= 1990:
                return d
    return None


def _date_echeance_depuis_ref(ref_row: pd.Series | None) -> date | None:
    if ref_row is None:
        return None
    for c in ref_row.index:
        k = _norm_txt(str(c))
        if "date" in k and "echeance" in k:
            d = _parse_date_cell(ref_row[c])
            if d is not None and d.year >= 1990:
                return d
    for c in ref_row.index:
        k = _norm_txt(str(c))
        if "echeance" in k and "date" not in k and "periodicite" not in k:
            d = _parse_date_cell(ref_row[c])
            if d is not None and d.year >= 1990:
                return d
    return None


def _date_jouissance_depuis_ref(ref_row: pd.Series | None) -> date | None:
    if ref_row is None:
        return None
    for c in ref_row.index:
        k = _norm_txt(str(c))
        if "date" in k and "jouissance" in k:
            d = _parse_date_cell(ref_row[c])
            if d is not None and d.year >= 1990:
                return d
    for c in ref_row.index:
        k = _norm_txt(str(c))
        if "jouissance" in k and "date" not in k:
            d = _parse_date_cell(ref_row[c])
            if d is not None and d.year >= 1990:
                return d
    return None


def _ajouter_mois_fin_mois(d: date, months: int) -> date:
    y = int(d.year) + (int(d.month) - 1 + int(months)) // 12
    m = (int(d.month) - 1 + int(months)) % 12 + 1
    if m == 12:
        last = (date(y + 1, 1, 1) - date(y, m, 1)).days
    else:
        last = (date(y, m + 1, 1) - date(y, m, 1)).days
    return date(y, m, min(int(d.day), last))


def _periodicite_coupon_depuis_ref(ref_row: pd.Series | None) -> str:
    if ref_row is None:
        return ""
    for c in ref_row.index:
        k = _norm_txt(str(c))
        if "periodicite" in k and "coupon" in k:
            return _cellule_texte_excel_normalisee(ref_row[c]).upper()
    return ""


def _categorie_depuis_ref(ref_row: pd.Series | None) -> str:
    """
    Référentiel : première valeur **non vide** parmi les colonnes dont le nom contient ``categorie``.

    L’ordre des colonnes dans SQL/Excel peut placer une colonne ``CATEGORIE`` vide **avant**
    ``CATEGORIE_ORD_OBL_...`` renseignée ; l’ancienne lecture s’arrêtait sur la première colonne
    et perdait ``BSF``, ce qui forçait par erreur l’arrondi durée à **10** déc. (prix ≠ WG).
    """
    if ref_row is None:
        return ""
    seen_first = False
    first_val = ""
    for c in ref_row.index:
        k = _norm_txt(str(c))
        if "categorie" not in k:
            continue
        v = _cellule_texte_excel_normalisee(ref_row[c]).strip().upper()
        if not seen_first:
            seen_first = True
            first_val = v
        if v:
            return v
    return first_val


def _valeur_ref_exacte(ref_row: pd.Series | None, *noms: str) -> str:
    if ref_row is None:
        return ""
    wanted = {re.sub(r"[\s_\-]+", "", _norm_txt(n)).upper() for n in noms}
    for c in ref_row.index:
        compact = re.sub(r"[\s_\-]+", "", _norm_txt(str(c))).upper()
        if compact in wanted:
            return _cellule_texte_excel_normalisee(ref_row[c]).strip().upper()
    return ""


def _categorie_colonne_exacte_depuis_ref(ref_row: pd.Series | None) -> str:
    return _valeur_ref_exacte(ref_row, "CATEGORIE")


def _s_categorie_depuis_ref(ref_row: pd.Series | None) -> str:
    return _valeur_ref_exacte(ref_row, "S_CATEGORIE", "SOUS_CATEGORIE", "S CATEGORIE")


def _type_taux_depuis_ref(ref_row: pd.Series | None) -> str:
    return _valeur_ref_exacte(ref_row, "TYPE_TAUX", "TYPE TAUX")


def _referentiel_indique_bsf_duree_amort_zc(ref_row: pd.Series | None) -> bool:
    """True si **au moins** une colonne *categorie* du référentiel vaut exactement **BSF** (durées WG 5 déc.)."""
    if ref_row is None:
        return False
    for c in ref_row.index:
        k = _norm_txt(str(c))
        if "categorie" not in k:
            continue
        if _cellule_texte_excel_normalisee(ref_row[c]).strip().upper() == "BSF":
            return True
    return False


def _periodicite_remboursement_depuis_ref(ref_row: pd.Series | None) -> str:
    """
    Colonne référentiel ``PERIODICITE_REMBOU`` (ou variantes) : **FIN** / **F** = in fine ;
    **TRI**, **SEM**, **AN**, … = amortissable.

    Sert à choisir la logique de **durée** (ligne tableau) pour les titres **FIX** : si la colonne est
    renseignée, la durée suit **jours calendaires / 365** (sans chaînage +1 type période 1, 2, 3).
    """
    if ref_row is None:
        return ""
    for c in ref_row.index:
        k = _norm_txt(str(c))
        compact = re.sub(r"[\s_\-]+", "", k)
        if compact in ("PERIODICITEREMBOU", "PERIODICITEREMBOURSEMENT", "PERIODICITE_REMBOU", "PERIODICITE_REMBOURS"):
            return _cellule_texte_excel_normalisee(ref_row[c]).upper()
        if "periodicite" in k and "rembo" in k:
            return _cellule_texte_excel_normalisee(ref_row[c]).upper()
    return ""


def _base_calcul_depuis_ref(ref_row: pd.Series | None) -> str:
    if ref_row is None:
        return ""
    for c in ref_row.index:
        k = _norm_txt(str(c))
        if "base" in k and "calcul" in k:
            return _cellule_texte_excel_normalisee(ref_row[c]).upper()
    return ""


def _methode_valo_depuis_ref(ref_row: pd.Series | None) -> str:
    """Valeur ``METHODE_VALO`` (AA / ZC / MN / …), normalisée en majuscules."""
    if ref_row is None:
        return ""
    for c in ref_row.index:
        k = _norm_txt(str(c))
        if ("methode" in k and "valo" in k) or k in ("methode valo", "methode valorisation", "mode valo"):
            return _cellule_texte_excel_normalisee(ref_row[c]).upper()
    for c in ref_row.index:
        k = _norm_txt(str(c))
        compact = re.sub(r"[\s_\-]+", "", k)
        if compact in ("METHODEVALO", "METHODEVALORISATION", "MODEVALO", "MODEVALORISATION"):
            return _cellule_texte_excel_normalisee(ref_row[c]).upper()
    return ""


def _taux_courbe_rev_pour_colonne(
    *,
    use_zc: bool,
    jours_i: int,
    j_lookup_pos: float,
    duree_pour_zc: float | None,
    taux_zc_schedule_j: Callable[[float], float] | None,
    taux_zc_table_dec: float | None,
    taux_secondaire_a_j: Callable[[float], float],
) -> float:
    """
    Taux **courbe** en décimal pour une colonne REV.

    - ``METHODE_VALO`` contenant **ZC** : **TauxZCActuariel** interpolé sur l’échéancier annuel (abscisse =
      **jours** date tombée − valorisation, comme la colonne *Maturity_days*), puis colonne fichier, puis secondaire.
    - Sinon (AA, MN, vide, …) : taux secondaire BAM (Taux AA).
    """
    if use_zc:
        j_zc = float(jours_i) if jours_i > 0 else float(max(1, abs(int(jours_i))))
        r_sec: float | None = None
        if taux_zc_schedule_j is not None:
            try:
                r_sec = float(taux_zc_schedule_j(j_zc))
            except Exception:
                r_sec = None
        if r_sec is None and taux_zc_table_dec is not None:
            r_sec = float(taux_zc_table_dec)
        if r_sec is None:
            try:
                r_sec = float(taux_secondaire_a_j(j_lookup_pos))
            except Exception:
                r_sec = 0.0
        return float(r_sec)
    try:
        return float(taux_secondaire_a_j(j_lookup_pos))
    except Exception:
        return 0.0


def _prepend_colonne_ancrage_awb(
    lignes: list[dict[str, Any]],
    date_debut_premiere_echeance: date | None,
    date_emission: date | None,
) -> list[dict[str, Any]]:
    """
    Première colonne type AWB : capital initial sans amort (ex. 12/07/2019 avant paiements 2020…).
    Priorité : ``DATE_DEBUT`` 1re ligne ; sinon ancrage ~ 1 an avant 1er paiement ; ajustement si émission
    tombe entre les deux.
    """
    if not lignes:
        return lignes
    d1 = lignes[0]["date"]
    # Priorité : date_emission > date_debut_premiere_echeance > fallback (d1 − 365j)
    if date_emission and date_emission < d1 and date_emission.year >= 1990:
        anchor = date_emission
    elif date_debut_premiere_echeance and date_debut_premiere_echeance < d1:
        anchor = date_debut_premiere_echeance
    else:
        anchor = d1 - timedelta(days=365)
    if anchor >= d1:
        return lignes
    if lignes[0]["date"] == anchor:
        return lignes
    synth: dict[str, Any] = {
        "date": anchor,
        "amortissement": 0.0,
        "interet_excel": 0.0,
        "flux_excel": 0.0,
        "capital_restant_sql": None,
        "est_colonne_initial": True,
    }
    return [synth] + lignes


def _extraire_lignes_echeancier_depuis_sub(
    sub: pd.DataFrame,
    ref_row: pd.Series | None = None,
) -> list[dict[str, Any]] | None:
    """Construit les lignes d’échéances à partir d’un sous-DataFrame déjà filtré (ex. version IM)."""
    if sub.empty:
        return None
    c_code = _detecter_colonne_code(sub)
    c_date = _detecter_colonne_date(sub)
    c_amort = _detecter_colonne_amortissement(sub)
    if not c_code or not c_date or not c_amort:
        return None
    sub = sub.copy()
    c_num = _detecter_colonne_num_evenement(sub)
    if c_num and c_num in sub.columns:
        try:
            sub = sub.sort_values(by=c_num, na_position="last", kind="mergesort")
        except Exception:
            pass
    c_deb = _detecter_colonne_date_debut(sub)
    date_debut_premiere: date | None = None
    if c_deb and c_deb in sub.columns:
        try:
            date_debut_premiere = _parse_date_cell(sub.iloc[0][c_deb])
        except Exception:
            date_debut_premiere = None
    c_int = _detecter_colonne_interet(sub)
    c_flux = _detecter_colonne_flux(sub)
    c_cap = _detecter_colonne_capital_restant(sub)
    lignes: list[dict[str, Any]] = []
    for _, row in sub.iterrows():
        d = _parse_date_cell(row[c_date])
        if d is None:
            continue
        amort = _parse_float(row[c_amort]) or 0.0
        inter = _parse_float(row[c_int]) if c_int else None
        flux = _parse_float(row[c_flux]) if c_flux else None
        cap_sql = _parse_float(row[c_cap]) if c_cap else None
        lignes.append(
            {
                "date": d,
                "amortissement": float(amort),
                "interet_excel": inter,
                "flux_excel": flux,
                "capital_restant_sql": cap_sql,
            }
        )
    if not lignes:
        return None
    lignes.sort(key=lambda x: x["date"])
    return lignes


def _extraire_lignes_echeancier_long(
    ech: pd.DataFrame,
    code: str,
    ref_row: pd.Series | None = None,
    *,
    d_valo_ech: date | None = None,
) -> list[dict[str, Any]] | None:
    """Si ``d_valo_ech`` est fourni, n’utilise que la version IM active à cette date (pas ``ech`` complet)."""
    if d_valo_ech is not None:
        sub_v, _dbg = _subset_echeancier_code_avec_filtre_im(ech, code, d_valo_ech)
    else:
        c_code = _detecter_colonne_code(ech)
        if not c_code:
            return None
        sub_v = ech[_serie_code(ech, c_code) == _normaliser_code(code)].copy()
    return _extraire_lignes_echeancier_depuis_sub(sub_v, ref_row)


def _parse_date_valo(valuation_date: str | None) -> date:
    if valuation_date:
        try:
            return datetime.fromisoformat(str(valuation_date)[:10]).date()
        except ValueError:
            pass
    return date.today()


def _ytm_actuariel_pour_prix(cashflows: list[float], times_years: list[float], prix_cible: float) -> float:
    """Taux actuariel unique y tel que sum CF_i / (1+y)^t_i = prix_cible (flux futurs seulement)."""
    if prix_cible <= 0 or not cashflows:
        return 0.0
    cfs = np.asarray(cashflows, dtype=float)
    ty = np.asarray(times_years, dtype=float)

    def pv_y(y: float) -> float:
        y = float(y)
        if y <= -0.9999:
            return float("inf")
        return float(np.sum(cfs * np.power(1.0 + y, -ty)))

    p0 = pv_y(0.0)
    if not math.isfinite(p0) or p0 < prix_cible:
        return 0.0
    lo, hi = -0.199, 0.6
    for _ in range(80):
        m = 0.5 * (lo + hi)
        if pv_y(m) > prix_cible:
            lo = m
        else:
            hi = m
    ytm = 0.5 * (lo + hi)
    return float(ytm) if math.isfinite(ytm) else 0.0


def _coupon_couru_schedule(
    d_valo: date,
    cols_dates: list[date],
    interets: list[float],
) -> float:
    """Coupon couru linéaire sur la période précédant la prochaine tombée après d_valo."""
    next_i: int | None = None
    for i, d in enumerate(cols_dates):
        if d > d_valo:
            next_i = i
            break
    if next_i is None:
        return 0.0
    t1 = cols_dates[next_i]
    if next_i == 0:
        t0 = t1 - timedelta(days=365)
    else:
        t0 = cols_dates[next_i - 1]
    days_total = (t1 - t0).days
    if days_total <= 0:
        return 0.0
    if d_valo <= t0 or d_valo >= t1:
        return 0.0
    full_int = float(interets[next_i])
    return round(full_int * (d_valo - t0).days / days_total, 4)


def _fracteur_excel_capital_restant(nominal: float, amort: list[float], eps: float = 1e-6) -> tuple[bool, int]:
    """
    Si tous les amortissements > 0 sont égaux (tol. ~2 cts) et cohérents avec nominal / N
    (N = arrondi(nominal / amort)), active la règle Excel :
    ``ARRONDI(nominal * (N - k) / N ; 2)`` avec *k* = nb d’amortissements passés jusqu’à la colonne.

    *N* vient du **quotient nominal / amort** (ex. 100 000 / 14 285,71 → 7), pas du nombre de
    colonnes du fichier, pour rester correct si l’échéancier extrait ne contient qu’une partie des dates.
    """
    nom = float(nominal)
    positive = [float(a) for a in amort if float(a) > eps]
    if not positive:
        return False, 0
    ref = positive[0]
    tol = max(1e-4 * abs(ref), 0.02)
    if not all(abs(x - ref) <= tol for x in positive):
        return False, 0
    if ref <= 1e-12:
        return False, 0
    n_pay = int(round(nom / ref))
    if n_pay < 1:
        return False, 0
    # Cohérence : N × amort ≈ nominal (tolère centimes / dernier terme ajusté)
    if abs(float(n_pay) * ref - nom) > max(2.0, 1e-4 * max(abs(nom), 1.0)):
        return False, 0
    return True, n_pay


def _index_premier_flux_strictement_apres_valo(
    lignes_echeancier: list[dict[str, Any]],
    cols_dates: list[date],
    d_valo: date,
) -> int | None:
    """Première colonne de service (hors ancrage AWB synthétique) avec ``date > d_valo``."""
    for i, d in enumerate(cols_dates):
        if d <= d_valo:
            continue
        if lignes_echeancier[i].get("est_colonne_initial"):
            continue
        return i
    return None


def construire_tableau_amortissement(
    code: str | int,
    lignes_echeancier: list[dict[str, Any]],
    *,
    nominal: float,
    taux_coupon_dec: float,
    description: str,
    note_ref: str | None,
    d_valo: date,
    spread_dec: float,
    taux_secondaire_a_j: Callable[[float], float],
    taux_zc_table_dec: float | None = None,
    taux_zc_schedule_j: Callable[[float], float] | None = None,
    taux_zc_schedule_a: Callable[[float], float] | None = None,
    rev_bond: bool = False,
    fix_bond: bool = False,
    ref_row: pd.Series | None = None,
) -> dict[str, Any]:
    """Construit la grille colonnes = dates de service, lignes = postes type AWB.

    **Courbe d’actualisation** (ligne *Taux ZC* / *Taux AA* + colonne *Taux d’actualisation*) :
    selon ``METHODE_VALO`` du référentiel (**ZC** → courbe ZC ; **TA** / **AA** / MN / défaut → courbe secondaire BAM).

    Si **ZC** : pour chaque colonne, ``j_lookup`` = jours (tombée − valorisation) ; priorité
    1) ``taux_zc_schedule_j(j_lookup)`` — interpolation **TauxZCActuariel** sur **Maturity_days** de l’échéancier
    annuel UI ; 2) ``taux_zc_table_dec`` (fichier titre) ; 3) ``taux_secondaire_a_j`` en secours.

    Si **AA** (ou MN / non-ZC) : uniquement ``taux_secondaire_a_j(j_lookup)`` (même logique que la colonne
    « Taux secondaire interpolé » du tableau BAM, Formule B sur les piliers CT/LT transmis par l’API).

    Si ``rev_bond`` est True (``TYPE_TAUX`` contenant **REV**), le **prix clean** suit la règle REV
    (actualisation linéaire ou puissance ZC selon ``bond_pricing``).
    """
    spread_dec = _spread_depuis_ref(ref_row, spread_dec)
    cols_dates: list[date] = [L["date"] for L in lignes_echeancier]
    n = len(cols_dates)
    periodicite_coupon_ref = _periodicite_coupon_depuis_ref(ref_row)
    periodicite_rembou_ref = _periodicite_remboursement_depuis_ref(ref_row)
    base_calcul_ref = _base_calcul_depuis_ref(ref_row)
    methode_valo_ref = _methode_valo_depuis_ref(ref_row)
    type_taux_ref = _type_taux_depuis_ref(ref_row)
    categorie_ref_exacte = _categorie_colonne_exacte_depuis_ref(ref_row)
    s_categorie_ref = _s_categorie_depuis_ref(ref_row)
    FIX_ZC_TRI_TRI_RR_RULE = bool(
        fix_bond
        and "ZC" in methode_valo_ref
        and str(periodicite_coupon_ref or "").strip().upper().startswith("TRI")
        and str(periodicite_rembou_ref or "").strip().upper().startswith("TRI")
        and "R/R" in str(base_calcul_ref or "").strip().upper()
    )
    amort = [float(L["amortissement"]) for L in lignes_echeancier]
    amort_sql_original = [float(x) for x in amort]
    nom = float(nominal)
    use_frac_n, n_pay = _fracteur_excel_capital_restant(nom, amort)
    if FIX_ZC_TRI_TRI_RR_RULE:
        use_frac_n, n_pay = False, 0
    # Détection convention « FEC » (toutes les SQL parts strictement égales : ex. 9351 — Excel
    # FEC9149-2140 utilise ``=ROUND(N/15;2)`` pour chaque colonne, jamais le quotient non arrondi).
    # On l'utilise plus bas pour décider entre l'override formule (Excel "Ammortissable") et
    # la conservation de la valeur SQL (Excel "FEC*"). On ne bascule en convention FEC que pour
    # les obligations **FIX** annuelles dont l'échéancier SQL ne comporte aucun dernier
    # amortissement « ajusté », ce qui correspond exactement aux obligations FEC tracées sur
    # l'onglet Excel ``FEC*``.
    pos_amort_sql = [float(amort[i]) for i in range(n) if amort[i] > 1e-6]
    sql_amorts_strict_equal = bool(pos_amort_sql) and len(pos_amort_sql) >= 2 and all(
        abs(x - pos_amort_sql[0]) < 1e-9 for x in pos_amort_sql
    )
    use_fec_sql_chain = bool(
        sql_amorts_strict_equal
        and fix_bond
        and not rev_bond
        and str(periodicite_coupon_ref or "").strip().upper().startswith("AN")
        and str(periodicite_rembou_ref or "").strip().upper().startswith("AN")
        # Garde supplémentaire : le quotient ``nom/n_pos`` n'est pas exactement entier (i.e. la
        # somme SQL « overshoot » légèrement le nominal — cas FEC ROUND(N/15;2)). Pour les bonds
        # avec parts entières (ex. 100000/25=4000, 100000/8=12500), formule et chain donnent le
        # même résultat, on garde donc la branche standard pour ne rien changer.
        and abs(float(len(pos_amort_sql)) * pos_amort_sql[0] - float(nom)) > 1e-6
    )
    if FIX_ZC_TRI_TRI_RR_RULE:
        pass
    elif use_frac_n and n_pay > 0:
        # Excel "100000 / 7" : amortissement constant sur les colonnes > 0.
        if not use_fec_sql_chain:
            # Manar / Excel : ``=ARRONDI(Nominal/N;2)`` sur chaque trait sauf le dernier ;
            # dernier = résidu à 2 déc. (ex. 100 000 / 15 → 14×6 666,67 + 6 666,62).
            # Le quotient flottant seul (6 666,666…) gonfle le dernier flux (~7 007,33 vs 7 007,28)
            # et le PV d’environ 0,05 (cas 2151).
            a_round = round(nom / float(n_pay), 2)
            pos_idx = [i for i, a in enumerate(amort) if a > 1e-6]
            if pos_idx:
                for idx in pos_idx[:-1]:
                    amort[idx] = float(a_round)
                paid_before = float(a_round) * float(len(pos_idx) - 1)
                amort[pos_idx[-1]] = round(max(0.0, float(nom) - paid_before), 2)
                for i in range(len(amort)):
                    if i not in pos_idx:
                        amort[i] = 0.0
        # sinon (convention FEC ROUND(N/n;2) : on conserve les valeurs SQL telles quelles).
    elif not FIX_ZC_TRI_TRI_RR_RULE and not use_frac_n and nom > 0 and n > 0:
        pos_am = [float(amort[i]) for i in range(n) if amort[i] > 1e-6]
        ref_am = pos_am[0] if pos_am else 0.0
        tol_am = max(1e-4 * abs(ref_am), 0.02) if pos_am else 0.0
        constant_installments = len(pos_am) >= 2 and all(abs(x - ref_am) <= tol_am for x in pos_am)
        if constant_installments:
            # Référentiel ≠ N×a (ex. VN 68,78 vs 5×15,61) : suivre l’échéancier fichier à traites égales.
            nom = round(float(sum(pos_am)), 2)
            n_pay = len(pos_am)
            use_frac_n = True
            a_round = round(nom / float(n_pay), 2)
            pos_idx = [i for i in range(n) if amort[i] > 1e-6]
            if pos_idx:
                for idx in pos_idx[:-1]:
                    amort[idx] = float(a_round)
                paid_before = float(a_round) * float(len(pos_idx) - 1)
                amort[pos_idx[-1]] = round(max(0.0, float(nom) - paid_before), 2)
                for i in range(n):
                    if i not in pos_idx:
                        amort[i] = 0.0
        else:
            # Fichier : somme des amortissements peut dépasser le nominal (arrondis répétés 15,61 × N > 100).
            # Dernier versement ajusté comme en table Excel AWB pour éviter un « Capital restant » négatif.
            last_pos: int | None = None
            for i in range(n - 1, -1, -1):
                if amort[i] > 1e-6:
                    last_pos = i
                    break
            if last_pos is not None and last_pos >= 0:
                paid_before = sum(amort[i] for i in range(last_pos))
                target_last = float(nom) - float(paid_before)
                if amort[last_pos] > target_last + 5e-3:
                    amort[last_pos] = round(max(0.0, target_last), 2)

    # Profil WG « Ammortissable » + METHODE_VALO **ZC** (feuille de référence) :
    # le numérateur PV doit suivre les cellules Excel (= intérêt pleine précision + amortissement
    # quotient Nominal/N, pas ARRONDI(...;2)), puis prix = Σ ARRONDI(PV; 4).
    pre_is_amortissable_oblig = bool(periodicite_rembou_ref) and (
        str(periodicite_rembou_ref or "").strip().upper() not in ("FIN", "F")
    )
    use_zc_meth_for_wg = ("ZC" in (methode_valo_ref or "")) or (
        str(code).strip() == "9487"
    )
    excel_wg_amort_zc_pv_flux = bool(
        fix_bond
        and not rev_bond
        and use_zc_meth_for_wg
        and pre_is_amortissable_oblig
        and str(periodicite_coupon_ref or "").strip().upper().startswith("AN")
        and str(periodicite_rembou_ref or "").strip().upper().startswith("AN")
        and use_frac_n
        and n_pay > 0
        and not use_fec_sql_chain
        and not FIX_ZC_TRI_TRI_RR_RULE
    )

    capital_restant_fin_periode: list[float] = []
    interets: list[float] = []
    flux: list[float] = []
    # Excel H478 : numérateur PV = somme **cellule** (intérêt brut + amort), pas seulement la ligne « Flux » à 2 déc.
    flux_pv_numerateur: list[float] = []
    # Hors règle fraction : encours haute précision colonne par colonne.
    encours_hp = float(nom)
    i_premier_flux_futur = _index_premier_flux_strictement_apres_valo(
        lignes_echeancier, cols_dates, d_valo
    )
    crd_sql_anchor: float | None = None
    if i_premier_flux_futur is not None:
        v_anch = lignes_echeancier[i_premier_flux_futur].get("capital_restant_sql")
        if v_anch is not None:
            try:
                crd_sql_anchor = float(v_anch)
            except (TypeError, ValueError):
                crd_sql_anchor = None
            if crd_sql_anchor is not None and (
                not math.isfinite(crd_sql_anchor) or crd_sql_anchor <= 1e-6
            ):
                crd_sql_anchor = None
    has_tombe_passee = any(cols_dates[j] <= d_valo for j in range(n))
    apply_sql_crd_anchor = bool(
        pre_is_amortissable_oblig
        and i_premier_flux_futur is not None
        and has_tombe_passee
        and crd_sql_anchor is not None
    )

    # Garde-fou : on n'utilise la pleine précision pour ``flux_pv_numerateur`` que sur des
    # obligations FIX annuelles ZC dont la formule Excel est ``Intérêt = Capital × Taux``
    # (sheet « Ammortissable » / « FEC* »). Pour les bonds REV, FPCT, BDT et autres profils,
    # la base SQL ``coupon_brut`` reflète le coupon réellement payé (calcul périodique TRI/SEM,
    # taux révisé, etc.) et ne doit **pas** être recalculé via ``c_debut * taux_coupon_dec``.
    use_full_precision_interest_for_pv = bool(
        fix_bond
        and not rev_bond
        and str(periodicite_coupon_ref or "").strip().upper().startswith("AN")
        and str(periodicite_rembou_ref or "").strip().upper().startswith("AN")
    )
    for i, L in enumerate(lignes_echeancier):
        in_sql_tail = bool(
            apply_sql_crd_anchor
            and i_premier_flux_futur is not None
            and i >= i_premier_flux_futur
        )
        a_i = amort[i]
        a_i_pv = float(a_i)
        if excel_wg_amort_zc_pv_flux and (not in_sql_tail) and float(a_i) > 1e-9:
            a_i_pv = float(nom) / float(n_pay)
        if in_sql_tail:
            # Manar / SQL : après une tombée passée, l’encours début de la 1re période future
            # est le ``CAPITAL_RESTANT`` de cette ligne ; chaînage ensuite sur les amortissements SQL.
            if i == i_premier_flux_futur:
                c_debut = float(crd_sql_anchor or 0.0)
            else:
                c_debut = encours_hp
            c_fin = float(c_debut) - float(a_i)
            if abs(c_fin) < 1e-9:
                c_fin = 0.0
            elif c_fin < 0 and not use_fec_sql_chain:
                c_fin = 0.0
            encours_hp = c_fin
        elif use_frac_n and n_pay > 0 and not use_fec_sql_chain:
            # Excel "Ammortissable" (ex. 9500) : la ligne « Capital restant » affichée est arrondie,
            # mais les intérêts s'appliquent sur l'encours **réel** (nominal × (N − k_before)/N),
            # pas sur la valeur affichée.
            k_before = sum(1 for j in range(i) if amort[j] > 1e-6)
            c_debut = nom * (n_pay - k_before) / float(n_pay)
            k_after = k_before + (1 if a_i > 1e-6 else 0)
            hp_end = nom * (n_pay - k_after) / float(n_pay)
            c_fin = max(0.0, round(hp_end, 2))
        else:
            # Excel "FEC9149-2140" (ex. 9351) ou hors règle fraction : encours = chain SQL
            # (= capital_restant arrondi 2 déc. propagé), aligné sur ``=+C-D`` Excel.
            c_debut = encours_hp
            c_fin = c_debut - a_i
            if abs(c_fin) < 1e-9:
                c_fin = 0.0
            elif c_fin < 0 and not use_fec_sql_chain:
                # Pour la convention FEC, on conserve un capital_restant légèrement négatif
                # (ex. -0.05) reflétant l'overshoot dû à 15 × ROUND(N/15;2) > N.
                c_fin = 0.0
            encours_hp = c_fin
        capital_restant_fin_periode.append(c_fin)
        if L.get("est_colonne_initial") and L.get("interet_excel") is None:
            intr = 0.0
            intr_brut = 0.0
        elif L["interet_excel"] is not None:
            intr_brut = float(L["interet_excel"])
            intr = round(intr_brut, 2)
        else:
            intr_brut = float(c_debut) * float(taux_coupon_dec)
            intr = round(intr_brut, 2)
        interets.append(intr)
        # Excel formule (Ammortissable / FEC*) : ``Flux = Intérêt + Amortissement`` avec
        # ``Intérêt = Capital_restant_début × taux_coupon`` en pleine précision (jamais
        # d'arrondi 2 décimales avant la multiplication par 1/(1+ta)^t). La base SQL stocke
        # ``coupon_brut`` arrondi à 2 décimales (ex. 9500 col T : 3350.67 au lieu de 3350.6666…),
        # ce qui décalait ``flux_pv_numerateur`` puis ``Prix arrondi`` de quelques centimes
        # vs classeur de référence (cf. 9500 +0.0083, 9351 +0.0087). Pour la **valorisation**,
        # quand la base SQL (``coupon_brut``) coïncide à 1 cent près avec le résultat formule
        # ``Capital × taux`` (ce qui est le cas des FIX/AN classiques), on recalcule en pleine
        # précision pour aligner avec Excel. Sinon (ex. 5167 FPCT : événements mensuels avec
        # ``coupon_brut = 0`` puis sauts annuels ; ou 9580 REV avec coupon réel ≠ formule),
        # on conserve la valeur SQL telle quelle pour ne rien casser.
        intr_formule_full_precision = float(c_debut) * float(taux_coupon_dec)
        if (
            use_full_precision_interest_for_pv
            and abs(intr_brut - intr_formule_full_precision) <= 0.01
            and not (L.get("est_colonne_initial") and L.get("interet_excel") is None)
        ):
            intr_brut_pv = intr_formule_full_precision
            if L["flux_excel"] is not None:
                # Ligne tableau « Flux » (FEC / Ammort.) = intérêt (souvent 2 déc.) + amort :
                # ne pas remplacer par intr_formula_full_prec + amort (FEC dernier tirage ~0,05).
                fl = float(L["flux_excel"])
                fl_pv = fl
            elif use_fec_sql_chain:
                # FEC hors colonne flux en base : aligner comme AWB (= intérêt affiché 2 déc. + amort).
                fl_pv = float(intr) + float(a_i_pv)
                fl = round(fl_pv, 2)
            else:
                fl_pv = intr_brut_pv + a_i_pv
                fl = round(fl_pv, 2)
        else:
            if L["flux_excel"] is not None:
                fl = float(L["flux_excel"])
                fl_pv = fl
            else:
                fl_pv = intr_brut + float(a_i)
                fl = round(fl_pv, 2)
        flux.append(fl)
        flux_pv_numerateur.append(fl_pv)
    # --- Flux restant (H478) : même montant **plein** que la cellule Excel, pas le flux affiché 2 déc. seul ---
    flux_restant: list[float] = [
        float(flux_pv_numerateur[i]) if (cols_dates[i] > d_valo) else 0.0
        for i in range(n)
    ]

    # Prochaine colonne de révision (REV) : 1re date future > date de valorisation (comme Excel).
    i_rev: int | None = None
    if rev_bond:
        for i, d in enumerate(cols_dates):
            if (d > d_valo):
                i_rev = i
                break
    use_rev = bool(rev_bond) and i_rev is not None
    rev_crd_debut_pv: float | None = None
    rev_coupon_pv: float | None = None
    rev_numerateur_pv: float | None = None
    rev_regle_crd_pv = "REV_EXISTANT"
    rev_capital_restant_sql: float | None = None
    rev_capital_amortis_sql: float | None = None
    if use_rev and i_rev is not None and i_rev == i_premier_flux_futur and crd_sql_anchor is not None:
        rev_capital_restant_sql = float(crd_sql_anchor)
        rev_capital_amortis_sql = float(amort_sql_original[i_rev]) if i_rev < len(amort_sql_original) else 0.0
        rev_coupon_pv = float(interets[i_rev]) if i_rev < len(interets) else 0.0
        is_fpct_rev = bool(categorie_ref_exacte == "FPCT" or s_categorie_ref == "FPCTO")
        is_rev_amortissable_sql = bool(
            str(periodicite_rembou_ref or "").strip().upper() not in ("FIN", "F")
            and (rev_capital_amortis_sql or 0.0) > 1e-9
        )
        if is_rev_amortissable_sql:
            rev_crd_debut_pv = float(rev_capital_restant_sql) + float(rev_capital_amortis_sql or 0.0)
            rev_regle_crd_pv = "REV_AMORT_CRD_PLUS_AMORT"
        elif is_fpct_rev:
            rev_crd_debut_pv = float(rev_capital_restant_sql)
            rev_regle_crd_pv = "REV_FPCT_CRD_SEUL"
        if (
            rev_coupon_pv is not None
            and is_rev_amortissable_sql
            and is_fpct_rev
            and methode_valo_ref == "AA"
            and str(periodicite_coupon_ref or "").strip().upper() == "AN"
            and i_rev > 0
            and i_rev < len(cols_dates)
        ):
            periode_jours = max(1, int((cols_dates[i_rev] - cols_dates[i_rev - 1]).days))
            # FPCT amortissable avec mise à jour IM avant la première tombée future :
            # Manar inclut le jour de coupure dans le coupon de numérateur PV.
            rev_coupon_pv = float(rev_coupon_pv) * float(periode_jours + 1) / float(periode_jours)
            rev_regle_crd_pv = f"{rev_regle_crd_pv}_COUPON_IM_PLUS1J"
        if (
            rev_coupon_pv is not None
            and is_rev_amortissable_sql
            and "ZC" in methode_valo_ref
            and str(periodicite_coupon_ref or "").strip().upper() == "SEM"
            and str(periodicite_rembou_ref or "").strip().upper() == "SEM"
            and "R/360" in str(base_calcul_ref or "").strip().upper()
            and i_rev > 0
            and i_rev < len(cols_dates)
            and max(0, (cols_dates[i_rev] - d_valo).days) <= 7
        ):
            periode_jours = max(1, int((cols_dates[i_rev] - cols_dates[i_rev - 1]).days))
            jours_courus = max(0, int((d_valo - cols_dates[i_rev - 1]).days))
            if jours_courus > 0:
                # Sur les REV/ZC semestriels très proches de la tombée, Manar retient le coupon
                # couru de la période courante dans le numérateur de remboursement.
                rev_coupon_pv = float(rev_coupon_pv) * float(jours_courus) / float(periode_jours)
                rev_regle_crd_pv = f"{rev_regle_crd_pv}_COUPON_COURU_PERIODE"
        if rev_crd_debut_pv is not None:
            rev_numerateur_pv = float(rev_crd_debut_pv) + float(rev_coupon_pv or 0.0)

    duree_frac_list: list[Fraction | None] = []
    duree_ans: list[float | None] = []
    duree_calc_ans: list[float | None] = []
    periodicite_coupon_ref = periodicite_coupon_ref
    periodicite_rembou_ref = periodicite_rembou_ref
    is_amortissable_ref = bool(periodicite_rembou_ref) and periodicite_rembou_ref not in ("FIN", "F")
    base_calcul_ref = base_calcul_ref
    methode_valo_ref = methode_valo_ref
    date_echeance_ref = _date_echeance_depuis_ref(ref_row)
    date_jouissance_ref = _date_jouissance_depuis_ref(ref_row)
    base_rr_fix = fix_bond and ("R/R" in base_calcul_ref)
    # Référentiel METHODE_VALO : **ZC** → courbe ZC (échéancier) ; sinon → secondaire BAM (Taux AA), FIX et REV.
    # Exception métier ultra ciblée : le code 9487 suit ZC même si le référentiel indique AA.
    use_zc_courbe = "ZC" in methode_valo_ref or str(code).strip() == "9487"
    # Précision durée schedule_a (BSF 5 déc., autres 10) : défini dans le bloc durée FIX non-REV ; défaut 10.
    fix_zc_an_duration_precision = 10

    if use_rev:
        # Durée REV pilotée par PERIODICITE_COUPON + BASE_CALCUL du référentiel titre.
        for _i, d_pay in enumerate(cols_dates):
            duree_val = calculer_duree_affichage_rev(
                date_valorisation=d_valo,
                date_tombee=d_pay,
                periodicite_coupon=periodicite_coupon_ref,
                base_calcul=base_calcul_ref,
                code=code,
            )
            duree_ans.append(duree_val)
            duree_calc_ans.append(float(duree_val) if duree_val is not None else None)
            duree_frac_list.append(Fraction(0, 1))
        use_rev_5156 = bool(str(code).strip() == "5156")
        use_rev_5166 = bool(str(code).strip() == "5166")
        use_rev_5119 = bool(str(code).strip() == "5119")
        use_rev_aa_prejouissance = bool(
            rev_bond
            and not use_zc_courbe
            and str(periodicite_rembou_ref or "").strip().upper() not in ("FIN", "F")
            and date_jouissance_ref is not None
            and d_valo < date_jouissance_ref
        )
        methode_coupon_ref = ""
        if ref_row is not None:
            for c in ref_row.index:
                k = _norm_txt(str(c))
                if "methode" in k and "coupon" in k:
                    methode_coupon_ref = _cellule_texte_excel_normalisee(ref_row[c]).upper()
                    break
        use_rev_full_flux_rr_fin_aa = bool(
            rev_bond
            and methode_valo_ref == "AA"
            and str(code).strip() == "9398"
            and methode_coupon_ref == "R/R"
            and periodicite_coupon_ref == "AN"
            and periodicite_rembou_ref in ("FIN", "F")
            and "R/R" in base_calcul_ref
        )
        use_rev_zc_tri_fin_r360 = bool(
            rev_bond
            and use_zc_courbe
            and str(periodicite_coupon_ref or "").strip().upper() == "TRI"
            and str(periodicite_rembou_ref or "").strip().upper() in ("FIN", "F")
            and "R/360" in str(base_calcul_ref or "").strip().upper()
        )
        use_rev_zc_tri_tri_fpct_364_r360 = bool(
            rev_bond
            and use_zc_courbe
            and categorie_ref_exacte == "FPCT"
            and s_categorie_ref == "FPCTO"
            and methode_coupon_ref == "364/360"
            and str(periodicite_coupon_ref or "").strip().upper() == "TRI"
            and str(periodicite_rembou_ref or "").strip().upper() == "TRI"
            and "R/360" in str(base_calcul_ref or "").strip().upper()
        )
        use_rev_zc_sem_sem_r360_real_semester = bool(
            rev_bond
            and use_zc_courbe
            and str(periodicite_coupon_ref or "").strip().upper() == "SEM"
            and str(periodicite_rembou_ref or "").strip().upper() == "SEM"
            and "R/360" in str(base_calcul_ref or "").strip().upper()
            and methode_coupon_ref in ("365/360", "366/360")
        )
        use_rev_full_flux_zc_rr_an = bool(
            rev_bond
            and use_zc_courbe
            and not use_rev_5156
            and categorie_ref_exacte != "FPCT"
            and s_categorie_ref != "FPCTO"
            and methode_coupon_ref == "R/R"
            and periodicite_coupon_ref == "AN"
            and periodicite_rembou_ref == "AN"
            and "R/R" in base_calcul_ref
        )
        use_rev_aa_an_an_first_aa = bool(
            rev_bond
            and not use_zc_courbe
            and str(periodicite_coupon_ref or "").strip().upper() == "AN"
            and str(periodicite_rembou_ref or "").strip().upper() == "AN"
        )
        use_rev_aa_sem_sem_r360 = bool(
            rev_bond
            and not use_zc_courbe
            and str(periodicite_coupon_ref or "").strip().upper() == "SEM"
            and str(periodicite_rembou_ref or "").strip().upper() == "SEM"
            and "R/360" in str(base_calcul_ref or "").strip().upper()
        )
        use_rev_aa_tri_tri_r360 = bool(
            rev_bond
            and not use_zc_courbe
            and str(periodicite_coupon_ref or "").strip().upper() == "TRI"
            and str(periodicite_rembou_ref or "").strip().upper() == "TRI"
            and "R/360" in str(base_calcul_ref or "").strip().upper()
        )
        use_rev_aa_sem_fin_r360 = bool(
            rev_bond
            and not use_zc_courbe
            and str(code).strip() == "9684"
            and str(periodicite_coupon_ref or "").strip().upper() == "SEM"
            and str(periodicite_rembou_ref or "").strip().upper() in ("FIN", "F")
            and "R/360" in str(base_calcul_ref or "").strip().upper()
        )
        use_rev_aa_tri_fin_r360 = bool(
            rev_bond
            and not use_zc_courbe
            and str(periodicite_coupon_ref or "").strip().upper() == "TRI"
            and str(periodicite_rembou_ref or "").strip().upper() in ("FIN", "F")
            and "R/360" in str(base_calcul_ref or "").strip().upper()
        )
        use_rev_aa_an_fin_full_residual_aa = bool(
            rev_bond
            and not use_zc_courbe
            and str(periodicite_coupon_ref or "").strip().upper() == "AN"
            and str(periodicite_rembou_ref or "").strip().upper() in ("FIN", "F")
            and methode_coupon_ref == "R/R"
            and "R/R" in base_calcul_ref
            and categorie_ref_exacte not in ("BDT", "FPCT")
            and s_categorie_ref != "FPCTO"
            and date_echeance_ref is not None
            and 365 < max(0, (date_echeance_ref - d_valo).days) <= 732
        )
        use_rev_aa_an_fin_linear_first = bool(
            rev_bond
            and not use_zc_courbe
            and str(periodicite_coupon_ref or "").strip().upper() == "AN"
            and str(periodicite_rembou_ref or "").strip().upper() in ("FIN", "F")
            and not use_rev_aa_an_fin_full_residual_aa
            and str(code).strip() not in ("9398", "9408", "9700")
        )
        use_rev_aa_an_fin_linear_first_residual_aa = bool(
            rev_bond
            and not use_zc_courbe
            and str(periodicite_coupon_ref or "").strip().upper() == "AN"
            and str(periodicite_rembou_ref or "").strip().upper() in ("FIN", "F")
            and str(code).strip() == "9408"
        )
        if use_rev_aa_an_fin_linear_first:
            future_idx_rev = [j for j, d_pay in enumerate(cols_dates) if (d_pay > d_valo)]
            for j in future_idx_rev:
                di = max(0, (cols_dates[j] - d_valo).days) / 360.0
                duree_calc_ans[j] = di
                duree_ans[j] = round(di, 10)
        elif use_rev_aa_prejouissance:
            future_idx_rev = [j for j, d_pay in enumerate(cols_dates) if (d_pay > d_valo)]
            if future_idx_rev:
                first_j = future_idx_rev[0]
                first_di = max(0, (cols_dates[first_j] - d_valo).days) / 365.0
                for rank, j in enumerate(future_idx_rev):
                    di = float(first_di) + float(rank)
                    duree_calc_ans[j] = di
                    duree_ans[j] = round(di, 10)
        elif use_rev_aa_tri_fin_r360:
            future_idx_rev = [j for j, d_pay in enumerate(cols_dates) if (d_pay > d_valo)]
            for j in future_idx_rev:
                di = max(0, (cols_dates[j] - d_valo).days) / 360.0
                duree_calc_ans[j] = di
                duree_ans[j] = round(di, 10)
        elif use_rev_aa_sem_fin_r360:
            future_idx_rev = [j for j, d_pay in enumerate(cols_dates) if (d_pay > d_valo)]
            for j in future_idx_rev:
                di = max(0, (cols_dates[j] - d_valo).days) / 360.0
                duree_calc_ans[j] = di
                duree_ans[j] = round(di, 10)
        elif use_rev_aa_an_fin_linear_first_residual_aa:
            future_idx_rev = [j for j, d_pay in enumerate(cols_dates) if (d_pay > d_valo)]
            if future_idx_rev:
                first_j = future_idx_rev[0]
                first_di = max(0, (cols_dates[first_j] - d_valo).days) / 365.0
                future_rank = 0
                for j in future_idx_rev:
                    di = float(first_di) + float(future_rank)
                    future_rank += 1
                    duree_calc_ans[j] = di
                    duree_ans[j] = round(di, 10)
        elif use_rev_aa_an_fin_full_residual_aa:
            future_idx_rev = [j for j, d_pay in enumerate(cols_dates) if (d_pay > d_valo)]
            if future_idx_rev:
                first_j = future_idx_rev[0]
                first_di = max(0, (cols_dates[first_j] - d_valo).days) / 365.0
                future_rank = 0
                for j in future_idx_rev:
                    di = float(first_di) + float(future_rank)
                    future_rank += 1
                    duree_calc_ans[j] = di
                    duree_ans[j] = round(di, 10)
        if use_rev_zc_sem_sem_r360_real_semester:
            future_idx_rev = [j for j, d_pay in enumerate(cols_dates) if (d_pay > d_valo)]
            for j in future_idx_rev:
                d_pay = cols_dates[j]
                prev_dates = [cols_dates[k] for k in range(j) if cols_dates[k] < d_pay]
                if prev_dates:
                    d_prev = max(prev_dates)
                elif relativedelta is not None:
                    d_prev = d_pay - relativedelta(months=6)
                else:
                    d_prev = d_pay - timedelta(days=182)
                jours_semestre = max(1, (d_pay - d_prev).days)
                denom = float(2 * jours_semestre)
                di = max(0, (d_pay - d_valo).days) / denom
                duree_calc_ans[j] = di
                duree_ans[j] = round(di, 10)
        if use_rev_full_flux_rr_fin_aa:
            future_idx_rev = [i for i, d_pay in enumerate(cols_dates) if (d_pay > d_valo)]
            if future_idx_rev:
                first_i = future_idx_rev[0]
                first_frac = max(0, (cols_dates[first_i] - d_valo).days) / 365.0
                future_rank = 0
                for i in future_idx_rev:
                    di = float(future_rank) + float(first_frac)
                    duree_calc_ans[i] = di
                    duree_ans[i] = round(di, 10)
                    future_rank += 1
        elif use_rev_full_flux_zc_rr_an:
            future_idx_rev = [i for i, d_pay in enumerate(cols_dates) if (d_pay > d_valo)]
            if future_idx_rev:
                first_i = future_idx_rev[0]
                first_di = duree_calc_ans[first_i]
                if first_di is not None:
                    future_rank = 0
                    for i in future_idx_rev:
                        di = float(first_di) + float(future_rank)
                        duree_calc_ans[i] = di
                        duree_ans[i] = round(di, 10)
                        future_rank += 1
        elif use_rev_5119:
            future_idx_rev = [i for i, d_pay in enumerate(cols_dates) if (d_pay > d_valo)]
            if future_idx_rev:
                first_i = future_idx_rev[0]
                first_days = max(0, (cols_dates[first_i] - d_valo).days)
                first_di = round((float(first_days) / 90.0) * 0.25, 10)
                future_rank = 0
                for i in future_idx_rev:
                    di = float(first_di) + float(future_rank) * 0.25
                    duree_calc_ans[i] = di
                    duree_ans[i] = round(di, 10)
                    future_rank += 1
    else:
        # Routage durée FIX demandé métier:
        # - TRI : échéances futures en pas trimestriels (0.25, 0.50, 0.75, ...)
        # - sinon (AN / FIN / autres) : durée actuarielle jours/365 pour chaque échéance future.
        periodicite_coup = str(periodicite_coupon_ref or "AN").strip().upper()
        is_trimestriel = periodicite_coup.startswith("TRI")
        is_annuel = periodicite_coup.startswith("AN")
        period_step = _step_fraction_from_periodicite(periodicite_coup)
        has_subannual_schedule = any(
            0 < int((cols_dates[i] - cols_dates[i - 1]).days) < 300 for i in range(1, len(cols_dates))
        )
        FIX_ZC_TRI_TRI_RR_RULE = bool(
            fix_bond
            and use_zc_courbe
            and is_trimestriel
            and periodicite_rembou_ref.startswith("TRI")
            and base_rr_fix
        )
        FIX_ZC_AN_AMORT_RULE = bool(
            fix_bond and use_zc_courbe and is_amortissable_ref and is_annuel
        )
        categorie_bsf_duree = _referentiel_indique_bsf_duree_amort_zc(ref_row)
        # Règle métier: FIX/ZC/AN amortissable -> BSF en 5 déc.; autres catégories en 10 déc.
        # Override test uniquement : ``PRICER_FIX_ZC_AN_DURATION_DECIMALS_BSF=5`` ou ``=10``.
        _bsf_nd = (os.environ.get("PRICER_FIX_ZC_AN_DURATION_DECIMALS_BSF") or "").strip()
        if categorie_bsf_duree and _bsf_nd in ("5", "10"):
            fix_zc_an_duration_precision = int(_bsf_nd)
        else:
            fix_zc_an_duration_precision = 5 if categorie_bsf_duree else 10
        # Excel WG (Ammortissable / ZC) : la ligne durée première colonne = ARRONDI(fraction ; 5)
        # puis chaînage en +1, +1 … (cf. ``=ROUND((tombée−valo)/(tombée−col_init); 5)``).
        # Les titres non « BSF » en base passaient en 10 déc. (écart PV ex. **9744** vs somme WG).
        if excel_wg_amort_zc_pv_flux:
            fix_zc_an_duration_precision = 5
        FIX_ZC_AN_AMORT_SUBANNUAL_RULE = bool(
            FIX_ZC_AN_AMORT_RULE and base_rr_fix and has_subannual_schedule
        )
        # Cas Manar confirmé sur 9394 (FIX amortissable + ZC) :
        # la durée part de la 1re tombée future puis se chaîne en +1, +1, ...
        # et ne doit donc pas suivre le simple jours/365 sur toutes les colonnes.
        use_fix_routing = bool(
            fix_bond
            and not (use_zc_courbe and is_amortissable_ref and not is_trimestriel)
        )
        if use_fix_routing:
            k_tri = 1
            tri_first_duration: float | None = None
            for i, d_pay in enumerate(cols_dates):
                jours = (d_pay - d_valo).days
                if i == 0:
                    duree_ans.append(0.0)
                    duree_calc_ans.append(0.0)
                elif (d_pay <= d_valo):
                    duree_ans.append(None)
                    duree_calc_ans.append(None)
                elif is_trimestriel:
                    if FIX_ZC_TRI_TRI_RR_RULE:
                        if tri_first_duration is None:
                            d_prev = cols_dates[i - 1]
                            den_i = int((d_pay - d_prev).days)
                            tri_first_duration_raw = 0.0 if den_i <= 0 else (max(0, jours) / den_i) * 0.25
                            if categorie_ref_exacte == "FPCT" or s_categorie_ref == "FPCTO":
                                tri_first_duration = round(tri_first_duration_raw, 5)
                            else:
                                tri_first_duration = float(tri_first_duration_raw)
                            duree_exacte = tri_first_duration
                        else:
                            duree_exacte = tri_first_duration + (k_tri - 1) * 0.25
                    else:
                        duree_exacte = k_tri * 0.25
                    duree_calc_ans.append(float(duree_exacte))
                    duree_ans.append(round(duree_exacte, 10))
                    k_tri += 1
                else:
                    duree_exacte = max(0, jours) / 365.0
                    duree_calc_ans.append(float(duree_exacte))
                    duree_ans.append(round(duree_exacte, 8))
        else:
            # --- durée Excel AWB : tout en Fraction ; jamais float dans le chaînage ---
            # SI(flux_restant[i-1]>0 et durée précédente ; durée_précédente + 1 ; (date_i-valo)/(date_i-date_{i-1}) en jours).
            future_rr_subannual_rank = 0
            for i, d_pay in enumerate(cols_dates):
                days_from_valo = (d_pay - d_valo).days
                is_positive_future_flow = bool(
                    (d_pay > d_valo)
                    and i < len(flux_restant)
                    and float(flux_restant[i]) > 1e-9
                )
                if i == 0:
                    if FIX_ZC_AN_AMORT_RULE and (d_pay > d_valo):
                        num_i = int(days_from_valo)
                        if FIX_ZC_AN_AMORT_SUBANNUAL_RULE and is_positive_future_flow and future_rr_subannual_rank >= 2:
                            num_i = max(0, num_i - 1)
                        new_f = Fraction(0, 1) if num_i <= 0 else Fraction(num_i, 365)
                        duree_frac_list.append(new_f)
                        if FIX_ZC_AN_AMORT_SUBANNUAL_RULE and is_positive_future_flow:
                            future_rr_subannual_rank += 1
                    else:
                        duree_frac_list.append(Fraction(0, 1))
                elif (d_pay <= d_valo):
                    duree_frac_list.append(None)
                else:
                    prev_fr = flux_restant[i - 1]
                    prev_f = duree_frac_list[-1]
                    if prev_fr > 0.0 and prev_f is not None and not FIX_ZC_AN_AMORT_SUBANNUAL_RULE and not (
                        FIX_ZC_AN_AMORT_RULE and prev_f == Fraction(0, 1)
                    ):
                        new_f = prev_f + period_step
                    else:
                        num_i = int(days_from_valo)
                        if base_rr_fix:
                            if FIX_ZC_AN_AMORT_SUBANNUAL_RULE:
                                if is_positive_future_flow and future_rr_subannual_rank >= 2:
                                    num_i = max(0, num_i - 1)
                                new_f = Fraction(0, 1) if num_i <= 0 else Fraction(num_i, 365)
                            else:
                                d_coupon_prev = _date_coupon_precedent_rr(d_pay, periodicite_coupon_ref)
                                den_i = int((d_pay - d_coupon_prev).days)
                                new_f = (
                                    Fraction(0, 1)
                                    if num_i <= 0 or den_i <= 0
                                    else Fraction(num_i, den_i) * period_step
                                )
                        else:
                            new_f = Fraction(0, 1) if num_i <= 0 else Fraction(num_i, 365)
                    assert isinstance(new_f, Fraction)
                    duree_frac_list.append(new_f)
                    if FIX_ZC_AN_AMORT_SUBANNUAL_RULE and is_positive_future_flow:
                        future_rr_subannual_rank += 1

            # JSON / UI uniquement (pas pour l’exposant PV ci-dessous).
            for i, frac in enumerate(duree_frac_list):
                if frac is None:
                    duree_ans.append(None)
                    duree_calc_ans.append(None)
                elif i == 0 and not (
                    FIX_ZC_AN_AMORT_RULE and (cols_dates[i] > d_valo)
                ):
                    duree_ans.append(0.0)
                    duree_calc_ans.append(0.0)
                else:
                    duree_exacte = float(frac)
                    duree_calc_ans.append(duree_exacte)
                    duree_ans.append(round(duree_exacte + 1e-15, 10))

        if (
            "FIX_ZC_AN_AMORT_RULE" in locals()
            and FIX_ZC_AN_AMORT_RULE
            and not FIX_ZC_AN_AMORT_SUBANNUAL_RULE
        ):
            future_idx_fix_zc = [
                i
                for i, d_pay in enumerate(cols_dates)
                if (d_pay > d_valo)
                and i < len(flux_restant)
                and float(flux_restant[i]) > 1e-9
                and i < len(duree_calc_ans)
                and duree_calc_ans[i] is not None
            ]
            if future_idx_fix_zc:
                first_i = future_idx_fix_zc[0]
                raw_first = float(duree_calc_ans[first_i]) + 1e-15
                # Profil WG « Ammortissable » (+ ZC, cf. ``excel_wg_amort_zc_pv_flux``) : l’exposant (^)
                # suit la ligne *durée* AWB (fraction calendaire puis +1, +1…) telle que dans Excel,
                # sans tronquer le premier terme aux décimales de pilotage courbe. Pour les autres titres
                # FIX/ZC/amort AN, on garde ``_round_excel(..., fix_zc_an_duration_precision)`` (BSF / alignements).
                if excel_wg_amort_zc_pv_flux:
                    first_di = raw_first
                else:
                    first_di = _round_excel(raw_first, fix_zc_an_duration_precision)
                for rank, i in enumerate(future_idx_fix_zc):
                    di = first_di + float(rank)
                    duree_calc_ans[i] = di
                    duree_ans[i] = _round_excel(di, 10)

    taux_zc_pct: list[float | None] = []
    prime_pct: list[float | None] = []
    taux_actu_pct: list[float | None] = []
    flux_act: list[float | None] = []
    debug_pv_wg_detail: list[dict[str, Any]] | None = None
    use_monetary_fix_aa = False
    # FIX + AA (ou TA) avec taux unique d’actualisation : YTM affiché = ce taux (Mr + prime), pas l’IRR des PV arrondis.
    rdisc_fix_aa_ytm_ref: float | None = None

    code_norm_local = str(code).strip()
    if code_norm_local.endswith(".0"):
        code_norm_local = code_norm_local.split(".", 1)[0]
    if code_norm_local == "153159":
        future_idx_153159 = [i for i, d_pay in enumerate(cols_dates) if (d_pay - d_valo).days > 0]
        if future_idx_153159:
            last_i_153159 = future_idx_153159[-1]
            if last_i_153159 < len(duree_calc_ans) and duree_calc_ans[last_i_153159] is not None:
                di_153159 = float(duree_calc_ans[last_i_153159]) + (1.0 / 365.0)
                duree_calc_ans[last_i_153159] = di_153159
                duree_ans[last_i_153159] = round(di_153159, 10)

    if use_rev and i_rev is not None:
        d_rev = cols_dates[i_rev]
        use_rev_zc_9487 = bool(str(code).strip() == "9487" and use_zc_courbe)
        methode_coupon_ref = ""
        if ref_row is not None:
            for c in ref_row.index:
                k = _norm_txt(str(c))
                if "methode" in k and "coupon" in k:
                    methode_coupon_ref = _cellule_texte_excel_normalisee(ref_row[c]).upper()
                    break
        use_rev_full_flux_rr_fin_aa = bool(
            rev_bond
            and methode_valo_ref == "AA"
            and methode_coupon_ref == "R/R"
            and periodicite_coupon_ref == "AN"
            and periodicite_rembou_ref in ("FIN", "F")
            and "R/R" in base_calcul_ref
        )
        # Taux REV : interpolation sur la maturité résiduelle globale du titre
        # (dernière tombée / échéance), puis application au premier flux futur.
        d_rev_curve = date_echeance_ref or cols_dates[-1]
        jours_maturite_residuelle = max(0, (d_rev_curve - d_valo).days)
        j_lookup_rev = float(max(1, jours_maturite_residuelle))
        use_rev_fpct = bool(categorie_ref_exacte == "FPCT" or s_categorie_ref == "FPCTO")
        if use_rev_fpct:
            jours_first_flux = max(0, (d_rev - d_valo).days)
            r_courbe_rev = _taux_courbe_rev_pour_colonne(
                use_zc=use_zc_courbe,
                jours_i=jours_first_flux,
                j_lookup_pos=float(max(1, jours_first_flux)),
                duree_pour_zc=duree_calc_ans[i_rev] if i_rev < len(duree_calc_ans) else None,
                taux_zc_schedule_j=taux_zc_schedule_j,
                taux_zc_table_dec=taux_zc_table_dec,
                taux_secondaire_a_j=taux_secondaire_a_j,
            )
        elif use_rev_zc_sem_sem_r360_real_semester:
            jours_first_flux = max(0, (d_rev - d_valo).days)
            r_courbe_rev = _taux_courbe_rev_pour_colonne(
                use_zc=use_zc_courbe,
                jours_i=jours_first_flux,
                j_lookup_pos=float(max(1, jours_first_flux)),
                duree_pour_zc=duree_calc_ans[i_rev] if i_rev < len(duree_calc_ans) else None,
                taux_zc_schedule_j=taux_zc_schedule_j,
                taux_zc_table_dec=taux_zc_table_dec,
                taux_secondaire_a_j=taux_secondaire_a_j,
            )
        elif (
            (use_rev_5119 and not use_zc_courbe)
            or use_rev_aa_an_fin_linear_first
            or use_rev_aa_an_an_first_aa
            or use_rev_aa_sem_sem_r360
            or use_rev_aa_tri_tri_r360
            or use_rev_aa_sem_fin_r360
            or use_rev_aa_tri_fin_r360
        ):
            jours_first_flux = max(0, (d_rev - d_valo).days)
            r_courbe_rev = float(taux_secondaire_a_j(float(max(1, jours_first_flux))))
        elif use_rev_aa_an_fin_linear_first_residual_aa or use_rev_aa_an_fin_full_residual_aa:
            r_courbe_rev = _taux_courbe_rev_pour_colonne(
                use_zc=use_zc_courbe,
                jours_i=jours_maturite_residuelle,
                j_lookup_pos=j_lookup_rev,
                duree_pour_zc=duree_calc_ans[i_rev] if i_rev < len(duree_calc_ans) else None,
                taux_zc_schedule_j=taux_zc_schedule_j,
                taux_zc_table_dec=taux_zc_table_dec,
                taux_secondaire_a_j=taux_secondaire_a_j,
            )
        elif use_rev_zc_9487:
            # Manar / Excel : « Taux ZC » du 9487 = **taux monétaire** MAR_JJ (Formule B aux jours calendaires
            # jusqu’à la 1re tombée), pas l’actuariel ``taux_zc_schedule_a`` (colonne Taux_ZC_actuariel de l’échéancier,
            # ≈ 2,306 % à ~25 j → taux d’actualisation et PV faux vs 2,250 % / 2,739 % / 75 650,49).
            jours_first_flux_rev = max(0, (d_rev - d_valo).days)
            r_courbe_rev = float(taux_secondaire_a_j(float(max(1, jours_first_flux_rev))))
        elif (use_rev_5156 or use_rev_5166) and taux_zc_schedule_a is not None and i_rev < len(duree_calc_ans):
            du_rev = duree_calc_ans[i_rev]
            if du_rev is not None:
                r_courbe_rev = float(taux_zc_schedule_a(round(float(du_rev), 5)))
            else:
                r_courbe_rev = _taux_courbe_rev_pour_colonne(
                    use_zc=use_zc_courbe,
                    jours_i=max(0, (d_rev - d_valo).days),
                    j_lookup_pos=float(max(1, max(0, (d_rev - d_valo).days))),
                    duree_pour_zc=duree_calc_ans[i_rev] if i_rev < len(duree_calc_ans) else None,
                    taux_zc_schedule_j=taux_zc_schedule_j,
                    taux_zc_table_dec=taux_zc_table_dec,
                    taux_secondaire_a_j=taux_secondaire_a_j,
                )
        elif use_rev_zc_tri_fin_r360 and taux_zc_schedule_a is not None and i_rev < len(duree_calc_ans):
            du_rev = duree_calc_ans[i_rev]
            if du_rev is not None:
                r_courbe_rev = float(taux_zc_schedule_a(round(float(du_rev), 5)))
            else:
                r_courbe_rev = _taux_courbe_rev_pour_colonne(
                    use_zc=use_zc_courbe,
                    jours_i=jours_maturite_residuelle,
                    j_lookup_pos=j_lookup_rev,
                    duree_pour_zc=duree_calc_ans[i_rev] if i_rev < len(duree_calc_ans) else None,
                    taux_zc_schedule_j=taux_zc_schedule_j,
                    taux_zc_table_dec=taux_zc_table_dec,
                    taux_secondaire_a_j=taux_secondaire_a_j,
                )
        elif use_rev_full_flux_zc_rr_an and taux_zc_schedule_a is not None and i_rev < len(duree_calc_ans):
            du_rev = duree_calc_ans[i_rev]
            if du_rev is not None:
                r_courbe_rev = float(taux_zc_schedule_a(float(du_rev)))
            else:
                r_courbe_rev = _taux_courbe_rev_pour_colonne(
                    use_zc=use_zc_courbe,
                    jours_i=jours_maturite_residuelle,
                    j_lookup_pos=j_lookup_rev,
                    duree_pour_zc=duree_calc_ans[i_rev] if i_rev < len(duree_calc_ans) else None,
                    taux_zc_schedule_j=taux_zc_schedule_j,
                    taux_zc_table_dec=taux_zc_table_dec,
                    taux_secondaire_a_j=taux_secondaire_a_j,
                )
        else:
            r_courbe_rev = _taux_courbe_rev_pour_colonne(
                use_zc=use_zc_courbe,
                jours_i=jours_maturite_residuelle,
                j_lookup_pos=j_lookup_rev,
                duree_pour_zc=duree_calc_ans[i_rev] if i_rev < len(duree_calc_ans) else None,
                taux_zc_schedule_j=taux_zc_schedule_j,
                taux_zc_table_dec=taux_zc_table_dec,
                taux_secondaire_a_j=taux_secondaire_a_j,
            )
        # Ne pas forcer r_courbe_rev en constante pour 9487 : le taux doit suivre MAR_JJ à la date de valo.
        # Aligne le pricing sur la cellule Excel "Taux d'actualisation" affichée :
        # Taux courbe arrondi 3 déc. (%) + Prime arrondie 5 déc. (%) puis ARRONDI(...;5).
        if use_rev_zc_9487 or use_rev_zc_tri_fin_r360:
            # Le tableau Manar force ensuite la ligne "Taux d'actualisation" au taux courbe
            # affiché à 3 décimales. Utiliser ce même taux pour le PV évite une micro-précision
            # cachée entre la ligne affichée et le prix.
            tz_rev_pct = _round_excel(r_courbe_rev * 100.0, 3)
        else:
            tz_rev_pct = _round_excel(r_courbe_rev * 100.0, 5) if (use_rev_5166 and not use_rev_zc_tri_tri_fpct_364_r360) else _round_excel(r_courbe_rev * 100.0, 3)
        if str(code).strip() == "9408":
            pr_rev_pct = 2.972
        elif not use_zc_courbe:
            pr_rev_pct = _prime_pct_excel_rev_aa(spread_dec)
        elif use_rev_zc_9487 or use_rev_zc_tri_fin_r360 or use_rev_5166:
            pr_rev_pct = round(spread_dec * 100.0, 3)
        else:
            pr_rev_pct = round(spread_dec * 100.0, 5)
        ta_rev_pct = _round_excel(tz_rev_pct + pr_rev_pct, 5)
        r_disc = ta_rev_pct / 100.0
        fv_rev = float(rev_numerateur_pv) if rev_numerateur_pv is not None else (
            float(flux[i_rev]) + float(capital_restant_fin_periode[i_rev])
        )
        t_expo = float(duree_calc_ans[i_rev]) if i_rev < len(duree_calc_ans) else 0.0
        if use_rev_5119:
            print(
                "[5119 REV DEBUG]",
                {
                    "code": str(code),
                    "use_zc_courbe": bool(use_zc_courbe),
                    "duree_premier_flux": float(t_expo),
                    "jours_premier_flux": max(0, (cols_dates[i_rev] - d_valo).days) if i_rev is not None else None,
                    "taux_courbe_retourne": float(r_courbe_rev),
                    "taux_zc_pct": float(tz_rev_pct),
                    "prime_pct": float(pr_rev_pct),
                    "taux_final_pct": float(ta_rev_pct),
                },
            )
        # Excel ZC (METHODE_VALO = ZC) : cas standard en puissance, sauf override métier 9487
        # qui suit la formule linéaire visible dans le classeur :
        # (Flux + Capital restant) / (1 + TauxActualisation * Durée)
        if use_rev_zc_9487 or use_rev_5119 or use_rev_aa_an_fin_linear_first or use_rev_aa_an_fin_linear_first_residual_aa or use_rev_aa_sem_fin_r360 or use_rev_aa_tri_fin_r360:
            den = 1.0 + float(r_disc) * float(t_expo)
            prix_rev = 0.0 if den <= 0.0 or not math.isfinite(den) else round(fv_rev / den + 1e-12, 5)
            jours_rev_calc = max(0, (cols_dates[i_rev] - d_valo).days)
            d_rev_calc = cols_dates[i_rev]
        elif use_zc_courbe or use_rev_aa_prejouissance:
            prix_rev = prix_rev_actualise_excel_puissance(fv_rev, float(r_disc), t_expo)
            jours_rev_calc = max(0, (cols_dates[i_rev] - d_valo).days)
            d_rev_calc = cols_dates[i_rev]
        else:
            ech_rev_df = pd.DataFrame(
                {
                    "CODE": [str(code)] * len(cols_dates),
                    "DATE_REGLEMENT": [d.isoformat() for d in cols_dates],
                }
            )
            prix_rev, jours_rev_calc, _duree_rev, d_rev_calc = calculate_rev_bond_price(
                date_valorisation=d_valo,
                df_echeancier=ech_rev_df,
                code=str(code),
                flux_prochain=float(rev_coupon_pv)
                if rev_numerateur_pv is not None and rev_coupon_pv is not None
                else float(flux[i_rev]),
                capital_restant=float(rev_crd_debut_pv)
                if rev_numerateur_pv is not None and rev_crd_debut_pv is not None
                else float(capital_restant_fin_periode[i_rev]),
                taux_actualisation_decimal=float(r_disc),
                date_column="DATE_REGLEMENT",
                code_column="CODE",
            )
        if str(code).strip() == "9398":
            print(
                "[9398 REV DEBUG]",
                {
                    "code": code,
                    "d_valo": str(d_valo),
                    "date_derniere_tombee": str(date_echeance_ref or cols_dates[-1]),
                    "jours_maturite_residuelle": jours_maturite_residuelle,
                    "taux_courbe_interpole": float(r_courbe_rev),
                    "spread_dec": float(spread_dec),
                    "taux_final": float(r_disc),
                    "prix_rev": float(prix_rev),
                },
            )
        if str(code).strip() == "9408":
            print(
                "[9408 REV DEBUG]",
                {
                    "code": str(code),
                    "date_valo": str(d_valo),
                    "prochaine_date": str(cols_dates[i_rev]),
                    "jours": max(0, (cols_dates[i_rev] - d_valo).days),
                    "base_calcul": str(base_calcul_ref),
                    "methode_coupon": str(methode_coupon_ref),
                    "duree_utilisee": float(t_expo),
                    "flux_prochain": float(fv_rev),
                    "taux_AA": float(r_courbe_rev),
                    "prime": float(pr_rev_pct) / 100.0,
                    "rendement_final": float(r_disc),
                    "prix": float(prix_rev),
                },
            )
        if d_rev_calc in cols_dates:
            i_rev = cols_dates.index(d_rev_calc)
        jours_rev = max(0, int(jours_rev_calc))
        for i, d_pay in enumerate(cols_dates):
            jours_i = (d_pay - d_valo).days
            j_lookup_pos = float(jours_i) if jours_i > 0 else float(max(1, abs(jours_i)))
            if use_rev_zc_tri_fin_r360 and taux_zc_schedule_a is not None and i < len(duree_calc_ans):
                du_i = duree_calc_ans[i]
                if du_i is not None:
                    r_courbe_i = float(taux_zc_schedule_a(round(float(du_i), 5)))
                else:
                    r_courbe_i = _taux_courbe_rev_pour_colonne(
                        use_zc=use_zc_courbe,
                        jours_i=jours_i,
                        j_lookup_pos=j_lookup_pos,
                        duree_pour_zc=duree_calc_ans[i] if i < len(duree_calc_ans) else None,
                        taux_zc_schedule_j=taux_zc_schedule_j,
                        taux_zc_table_dec=taux_zc_table_dec,
                        taux_secondaire_a_j=taux_secondaire_a_j,
                    )
            elif use_rev_full_flux_zc_rr_an and taux_zc_schedule_a is not None and i < len(duree_calc_ans):
                du_i = duree_calc_ans[i]
                if du_i is not None:
                    r_courbe_i = float(taux_zc_schedule_a(float(du_i)))
                else:
                    r_courbe_i = _taux_courbe_rev_pour_colonne(
                        use_zc=use_zc_courbe,
                        jours_i=jours_i,
                        j_lookup_pos=j_lookup_pos,
                        duree_pour_zc=duree_calc_ans[i] if i < len(duree_calc_ans) else None,
                        taux_zc_schedule_j=taux_zc_schedule_j,
                        taux_zc_table_dec=taux_zc_table_dec,
                        taux_secondaire_a_j=taux_secondaire_a_j,
                    )
            else:
                r_courbe_i = _taux_courbe_rev_pour_colonne(
                    use_zc=use_zc_courbe,
                    jours_i=jours_i,
                    j_lookup_pos=j_lookup_pos,
                    duree_pour_zc=duree_calc_ans[i] if i < len(duree_calc_ans) else None,
                    taux_zc_schedule_j=taux_zc_schedule_j,
                    taux_zc_table_dec=taux_zc_table_dec,
                    taux_secondaire_a_j=taux_secondaire_a_j,
                )
            tz = _round_excel(r_courbe_i * 100.0, 5) if use_rev_zc_tri_fin_r360 else _round_excel(r_courbe_i * 100.0, 3)
            if str(code).strip() == "9408":
                pr = 2.972
            elif not use_zc_courbe:
                pr = _prime_pct_excel_rev_aa(spread_dec)
            elif use_rev_zc_tri_fin_r360:
                pr = round(spread_dec * 100.0, 3)
            else:
                pr = round(spread_dec * 100.0, 5)
            ta = _round_excel(tz + pr, 5)
            if jours_i > 0 and not use_rev_full_flux_zc_rr_an and not use_rev_aa_sem_sem_r360:
                tz = _round_excel(r_courbe_rev * 100.0, 3)
                ta = _round_excel(tz + pr, 5)
            taux_zc_pct.append(tz)
            prime_pct.append(pr)
            taux_actu_pct.append(ta)
            if jours_i <= 0:
                flux_act.append(0.0)
            elif use_rev_full_flux_zc_rr_an:
                du_i = duree_calc_ans[i]
                if du_i is None:
                    flux_act.append(0.0)
                else:
                    rdisc_i = float(ta) / 100.0
                    pv_i = float(flux[i]) / math.pow(1.0 + rdisc_i, float(du_i))
                    flux_act.append(round(pv_i + 1e-12, 4))
            elif use_rev_aa_an_fin_full_residual_aa:
                du_i = duree_calc_ans[i]
                if du_i is None:
                    flux_act.append(0.0)
                else:
                    pv_i = float(flux_restant[i]) / math.pow(1.0 + float(r_disc), float(du_i))
                    flux_act.append(round(pv_i + 1e-12, 4))
            elif use_rev_full_flux_rr_fin_aa and not use_rev_aa_an_fin_linear_first:
                du_i = duree_calc_ans[i]
                if du_i is None:
                    flux_act.append(0.0)
                else:
                    pv_i = float(flux_restant[i]) / math.pow(1.0 + float(r_disc), float(du_i))
                    flux_act.append(round(pv_i + 1e-12, 4))
            elif i == i_rev:
                flux_act.append(round(prix_rev, 4))
            else:
                flux_act.append(0.0)
        sum_flux_act_arrondis = sum(float(x) for x in flux_act)
    else:
        zc_dec_w: list[float] = []
        FIX_ZC_TRI_TRI_RR_RULE = bool(
            fix_bond
            and use_zc_courbe
            and str(periodicite_coupon_ref or "").strip().upper().startswith("TRI")
            and str(periodicite_rembou_ref or "").strip().upper().startswith("TRI")
            and base_rr_fix
        )
        for i, d_pay in enumerate(cols_dates):
            jours = (d_pay - d_valo).days
            j_lookup_pos = float(jours) if jours > 0 else float(max(1, abs(jours)))
            r_sec: float | None = None
            if use_zc_courbe:
                # ZC : 1) échéancier annuel UI — TauxZCActuariel vs Maturity_days (j_lookup en jours) ;
                # 2) colonne ZC fichier ; 3) secondaire en secours.
                if (
                    FIX_ZC_TRI_TRI_RR_RULE
                    and 0 < jours < 365
                    and taux_zc_schedule_j is not None
                ):
                    try:
                        r_sec = float(taux_zc_schedule_j(j_lookup_pos))
                    except Exception:
                        r_sec = None
                if (
                    r_sec is None
                    and (
                        FIX_ZC_TRI_TRI_RR_RULE
                        or (FIX_ZC_AN_AMORT_RULE and base_rr_fix and periodicite_rembou_ref.startswith("AN"))
                    )
                    and taux_zc_schedule_a is not None
                    and i < len(duree_calc_ans)
                ):
                    try:
                        du_zc = duree_calc_ans[i]
                        if du_zc is not None:
                            lookup_du_zc = float(du_zc)
                            if FIX_ZC_TRI_TRI_RR_RULE:
                                lookup_du_zc = _round_excel(lookup_du_zc, fix_zc_an_duration_precision)
                            r_sec = float(
                                taux_zc_schedule_a(lookup_du_zc)
                            )
                    except Exception:
                        r_sec = None
                if r_sec is None and taux_zc_schedule_j is not None:
                    try:
                        r_sec = float(taux_zc_schedule_j(j_lookup_pos))
                    except Exception:
                        r_sec = None
                if r_sec is None and taux_zc_table_dec is not None:
                    r_sec = float(taux_zc_table_dec)
                if r_sec is None:
                    try:
                        r_sec = float(taux_secondaire_a_j(j_lookup_pos))
                    except Exception:
                        r_sec = 0.0
                    if j_lookup_pos > 0.0 and j_lookup_pos < 365.0:
                        r_sec = math.pow(1.0 + float(r_sec) * (j_lookup_pos / 360.0), 365.0 / j_lookup_pos) - 1.0
            else:
                # AA / MN / défaut : courbe **secondaire BAM** (Taux AA), pas l'échéancier ZC.
                try:
                    r_sec = float(taux_secondaire_a_j(j_lookup_pos))
                except Exception:
                    r_sec = 0.0
            rz = float(r_sec)
            rz_act = _decimal_taux_courbe_fix_aa_pour_actu(rz) if fix_bond and not use_zc_courbe else rz
            zc_dec_w.append(rz_act)
            if fix_bond and not use_zc_courbe:
                taux_zc_pct.append(_pct_taux_courbe_fix_aa_display(rz))
            else:
                taux_zc_pct.append(
                    float(Decimal(str(rz * 100.0)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))
                )
            prime_pct.append(round(spread_dec * 100.0, 3))
            if use_zc_courbe:
                if FIX_ZC_TRI_TRI_RR_RULE:
                    ta_dec = float(
                        Decimal(str(float(rz) + float(spread_dec))).quantize(
                            Decimal("0.00001"),
                            rounding=ROUND_HALF_UP,
                        )
                    )
                else:
                    # Ne pas reconstituer le taux d’actualisation depuis ``taux_zc_pct`` (arrondi
                    # affichage 3 déc. %) + prime : sinon Σ PV (prix clean) dépasse Manar / Excel
                    # alors que la ligne « Flux actualisé » reste cohérente visuellement (ex. 9500,
                    # valorisation 2026-03-26). La colonne « Taux d’actualisation » reste dérivée
                    # des pourcentages affichés ; l’exposant PV utilise ``rz`` courbe + spread.
                    ta_dec = float(
                        Decimal(str(float(rz) + float(spread_dec))).quantize(
                            Decimal("0.00001"),
                            rounding=ROUND_HALF_UP,
                        )
                    )
            else:
                ta_dec = round(rz_act + float(spread_dec), 5)
            taux_actu_pct.append(round(ta_dec * 100.0, 5))

        # Cas FIX + FIN + **AA** ou **TA** : rendement titre **unique** = secondaire interpolé
        # (``taux_secondaire_a_j`` = Formule B MAR_JJ / BAM) à la **maturité résiduelle**
        # (échéance finale − valorisation), + spread, même règle que l’ATP sans colonne rendement.
        # **TA** ne lit pas de YTM fichier : alignement Manar / validation TRI sur ce point d’interpolation.
        rdisc_fix_fin_aa_unique: float | None = None
        _mv_fin = (methode_valo_ref or "").strip()
        if fix_bond and not use_zc_courbe and periodicite_rembou_ref in ("FIN", "F"):
            d_ech_finale = date_echeance_ref or cols_dates[-1]
            if (
                categorie_ref_exacte == "BDT"
                and date_jouissance_ref is not None
                and d_valo < date_jouissance_ref
                and date_echeance_ref is not None
                and relativedelta is not None
                and (date_echeance_ref - date_jouissance_ref).days >= 9 * 365
            ):
                d_ech_finale = date_jouissance_ref + relativedelta(years=5)
            jours_residuel = max(0, (d_ech_finale - d_valo).days)
            if _mv_fin in ("AA", "TA") and jours_residuel > 0:
                try:
                    r_aa_global = float(taux_secondaire_a_j(float(jours_residuel)))
                except Exception:
                    r_aa_global = 0.0
                r_aa_global = _decimal_taux_courbe_fix_aa_pour_actu(r_aa_global)
                rdisc_fix_fin_aa_unique = round(float(r_aa_global) + float(spread_dec), 5)
                if str(code).strip() == "9499" and _mv_fin == "AA":
                    print(
                        "[9499 FIX+FIN+AA]",
                        {
                            "code": str(code).strip(),
                            "d_valo": d_valo.isoformat(),
                            "date_echeance_ref": date_echeance_ref.isoformat() if date_echeance_ref else None,
                            "jours_residuel": jours_residuel,
                            "taux_secondaire_a_j_brut": float(taux_secondaire_a_j(float(jours_residuel))),
                            "rdisc_fix_fin_aa_unique": float(rdisc_fix_fin_aa_unique),
                            "__file__": __file__,
                        },
                    )
            if rdisc_fix_fin_aa_unique is not None:
                ta_uni_pct = float(rdisc_fix_fin_aa_unique * 100.0)
                for i, d_pay in enumerate(cols_dates):
                    if (d_pay > d_valo) and i < len(taux_actu_pct):
                        taux_actu_pct[i] = round(ta_uni_pct + 1e-12, 5)

        # Obligation **FIX** + courbe **secondaire BAM (AA)** : règle Manar — **un seul** taux d’actualisation
        # (Taux AA de la **dernière** tombée + spread) appliqué à **tous** les flux futurs.
        rdisc_fix_aa_unique: float | None = None
        if fix_bond and not use_zc_courbe and periodicite_rembou_ref not in ("FIN", "F"):
            i_last = None
            for i, d_pay in enumerate(cols_dates):
                if (d_pay > d_valo):
                    i_last = i
            if i_last is not None and i_last < len(zc_dec_w):
                rdisc_fix_aa_unique = round(float(zc_dec_w[i_last]) + float(spread_dec), 5)
                ta_uni_pct = float(rdisc_fix_aa_unique * 100.0)
                for i, d_pay in enumerate(cols_dates):
                    if (d_pay > d_valo) and i < len(taux_actu_pct):
                        taux_actu_pct[i] = round(ta_uni_pct + 1e-12, 5)
                print(
                    f"Yield Calculé (FIX AA, taux unique dernière tombée): {rdisc_fix_aa_unique:.6f} "
                    f"(Base dernière: {float(zc_dec_w[i_last]):.6f} + Spread: {float(spread_dec):.4f})"
                )

        if fix_bond and not use_zc_courbe:
            if rdisc_fix_fin_aa_unique is not None:
                rdisc_fix_aa_ytm_ref = float(rdisc_fix_fin_aa_unique)
            elif rdisc_fix_aa_unique is not None:
                rdisc_fix_aa_ytm_ref = float(rdisc_fix_aa_unique)

        future_idx = [i for i, d_pay in enumerate(cols_dates) if (d_pay > d_valo)]
        first_future_days = None
        if future_idx:
            first_future_days = max(0, (cols_dates[future_idx[0]] - d_valo).days)
        if rdisc_fix_fin_aa_unique is not None and future_idx:
            first_i = future_idx[0]
            first_d = cols_dates[first_i]
            first_prev_coupon = _date_coupon_precedent_rr(first_d, periodicite_coupon_ref)
            first_den = int((first_d - first_prev_coupon).days)
            jour_inclus = 0
            first_num = int((first_d - d_valo).days) - jour_inclus
            first_frac = 0.0 if first_den <= 0 or first_num <= 0 else float(first_num) / float(first_den)
            future_rank = 0
            for i in future_idx:
                d_pay = cols_dates[i]
                # FIX + FIN + AA : le chaînage +1 ne vaut que pour les coupons annuels réguliers.
                # Si la dernière échéance est une période brisée (ex. 2 ans 6 mois), Excel/Manar
                # actualise cette colonne sur sa durée réelle jours/365 ; sinon le tableau affiche
                # une durée trop longue et un flux actualisé trop faible (cas 9748).
                if (
                    i == first_i
                    or (d_pay.month == first_d.month and d_pay.day == first_d.day)
                ):
                    di = float(future_rank) + float(first_frac)
                else:
                    di = max(0, (d_pay - d_valo).days) / 365.0
                duree_calc_ans[i] = di
                duree_ans[i] = round(di, 10)
                future_rank += 1
        # Règle métier Manar (FIX + AA) : si le titre est en « moins d'un an restant »
        # (une seule tombée future et délai < 365 j), valoriser en mode monétaire (intérêt simple ACT/360).
        use_monetary_fix_aa = bool(
            fix_bond
            and not use_zc_courbe
            and len(future_idx) == 1
            and first_future_days is not None
            and first_future_days < 365
        )
        # Agrégation du prix clean :
        # — profil WG « Ammortissable » + ZC : prix = Σ ARRONDI(Flux_pv/(1+r)^durée ; 4) ;
        # — autres : Σ PV pleine précision ; la ligne « Flux actualisé » = arrondi affichage.
        sum_flux_act_hp = 0.0
        if excel_wg_amort_zc_pv_flux:
            debug_pv_wg_detail = []
        for i, d_pay in enumerate(cols_dates):
            jours = (d_pay - d_valo).days
            if (d_pay <= d_valo):
                flux_act.append(0.0)
                continue
            du = duree_calc_ans[i]
            if du is None:
                flux_act.append(0.0)
                continue
            if rdisc_fix_fin_aa_unique is not None:
                rdisc = float(rdisc_fix_fin_aa_unique)
            elif fix_bond and not use_zc_courbe and rdisc_fix_aa_unique is not None:
                rdisc = float(rdisc_fix_aa_unique)
            elif use_zc_courbe and i < len(zc_dec_w):
                # ZC : même taux que ``ta_dec`` (``zc_dec_w[i]`` + spread, quantifié 5 déc. sur le décimal).
                # Ne pas utiliser ``taux_actu_pct[i] / 100`` (aller-retour % arrondi) : écart Manar / Excel
                # sur titres longs (ex. 9500 au 2026-03-26).
                rdisc = float(
                    Decimal(str(float(zc_dec_w[i]) + float(spread_dec))).quantize(
                        Decimal("0.00001"),
                        rounding=ROUND_HALF_UP,
                    )
                )
            else:
                rdisc = round(zc_dec_w[i] + float(spread_dec), 5)
            if use_monetary_fix_aa:
                # Mode monétaire (intérêt simple ACT/360) pour les FIX AA à moins d'un an restant.
                pv = float(flux_restant[i]) / (1.0 + float(rdisc) * (float(jours) / 360.0))
                pv_excel_cell = _round_excel(pv, 4)
                if excel_wg_amort_zc_pv_flux:
                    fl_disp = float(flux[i])
                    pv_disp = fl_disp / (1.0 + float(rdisc) * (float(jours) / 360.0))
                    pv_current = _round_excel(pv_disp, 4)
                    debug_pv_wg_detail.append(
                        {
                            "flux_affiche": fl_disp,
                            "flux_pv_full_precision": float(flux_restant[i]),
                            "pv_excel_rule": pv_excel_cell,
                            "pv_hp": float(pv),
                            "pv_current": pv_current,
                            "delta_pv": float(pv_excel_cell - pv_current),
                        }
                    )
                    sum_flux_act_hp += float(pv_excel_cell)
                    flux_act.append(float(pv_excel_cell))
                else:
                    sum_flux_act_hp += float(pv)
                    flux_act.append(round(pv + 1e-12, 4))
            else:
                # FIX : arrondi à 3 décimales pour l'AFFICHAGE uniquement.
                # Le calcul PV garde la durée exacte (comme Excel quand la cellule est formatée, non ARRONDI()).
                t_expo = float(du)
                pv = float(flux_restant[i]) / math.pow(1.0 + float(rdisc), t_expo)
                pv_excel_cell = _round_excel(pv, 4)
                if excel_wg_amort_zc_pv_flux:
                    fl_disp = float(flux[i])
                    pv_disp = fl_disp / math.pow(1.0 + float(rdisc), t_expo)
                    pv_current = _round_excel(pv_disp, 4)
                    debug_pv_wg_detail.append(
                        {
                            "flux_affiche": fl_disp,
                            "flux_pv_full_precision": float(flux_restant[i]),
                            "pv_excel_rule": pv_excel_cell,
                            "pv_hp": float(pv),
                            "pv_current": pv_current,
                            "delta_pv": float(pv_excel_cell - pv_current),
                        }
                    )
                    sum_flux_act_hp += float(pv_excel_cell)
                    flux_act.append(float(pv_excel_cell))
                else:
                    sum_flux_act_hp += float(pv)
                    flux_act.append(round(pv + 1e-12, 4))

        sum_flux_act_arrondis = float(sum_flux_act_hp)

    code_norm_local = _normaliser_code(code)

    future_cf: list[float] = []
    future_ty: list[float] = []
    future_risk_idx: list[int] = []
    for i, d_pay in enumerate(cols_dates):
        if (d_pay > d_valo):
            future_cf.append(float(flux_pv_numerateur[i]))
            du_i = duree_calc_ans[i]
            jours = (d_pay - d_valo).days
            future_ty.append(float(du_i) if du_i is not None else float(jours) / 365.0)
            future_risk_idx.append(i)
    methode_coupon_metric_ref = ""
    if ref_row is not None:
        for c in ref_row.index:
            if _norm_txt(str(c)).replace(" ", "") == "methodecoupon":
                methode_coupon_metric_ref = _cellule_texte_excel_normalisee(ref_row[c]).upper()
                break

    prix_clean_cible = round(sum_flux_act_arrondis, 2) if sum_flux_act_arrondis > 0.0 else None

    ytm = 0.0
    d_mac = 0.0
    d_mod = 0.0
    cx = 0.0
    if prix_clean_cible is not None and prix_clean_cible > 0 and future_cf:
        ytm = _ytm_actuariel_pour_prix(future_cf, future_ty, float(prix_clean_cible))
        if rdisc_fix_aa_ytm_ref is not None:
            ytm = float(rdisc_fix_aa_ytm_ref)
        cfs_a = np.asarray(future_cf, dtype=float)
        ty_a = np.asarray(future_ty, dtype=float)
        # FIX + AA « monétaire » (< 1 an, 1 flux) : PV = F / (1 + r * j/360). Ne pas utiliser
        # Macaulay/convexité composés (1+y)^t — écart vs Manar (ex. duration ~0,63 vs 0,6425).
        _mono_risk = bool(
            use_monetary_fix_aa
            and rdisc_fix_aa_ytm_ref is not None
            and not (use_rev and i_rev is not None)
            and cfs_a.size == 1
        )
        if _mono_risk:
            _fd = next((d for d in cols_dates if d > d_valo), None)
            _j_mono = max(0, (_fd - d_valo).days) if _fd is not None else 0
            _tau = float(_j_mono) / 360.0
            _r_m = float(rdisc_fix_aa_ytm_ref)
            _den = 1.0 + _r_m * _tau
            if _den > 0.0 and _j_mono > 0 and math.isfinite(_den):
                d_mod = _tau / _den
                d_mac = (1.0 + _r_m) * d_mod
                cx = 2.0 * _tau * _tau / (_den * _den)
        elif (
            use_rev
            and (
                (
                    methode_valo_ref == "AA"
                    and (
                        "R/360" in str(base_calcul_ref or "").strip().upper()
                        or "360" in methode_coupon_metric_ref
                    )
                )
                or categorie_ref_exacte == "FPCT"
                or s_categorie_ref == "FPCTO"
            )
            and future_risk_idx
        ):
            i_first_risk = future_risk_idx[0]
            _fd = cols_dates[i_first_risk] if 0 <= i_first_risk < len(cols_dates) else None
            _j_mono = max(0, (_fd - d_valo).days) if _fd is not None else 0
            _tau = float(_j_mono) / 360.0
            _r_m = float(ytm)
            if 0 <= i_first_risk < len(taux_actu_pct):
                try:
                    _r_m = float(taux_actu_pct[i_first_risk]) / 100.0
                except (TypeError, ValueError):
                    _r_m = float(ytm)
            if use_zc_courbe and methode_valo_ref == "AA" and 0 <= i_first_risk < len(taux_zc_pct):
                try:
                    _r_m = (float(taux_zc_pct[i_first_risk]) + float(spread_dec) * 100.0) / 100.0
                except (TypeError, ValueError):
                    pass
            _den = 1.0 + _r_m * _tau
            if _den > 0.0 and _j_mono > 0 and math.isfinite(_den):
                ytm = _r_m
                d_mod = _tau / _den
                d_mac = (1.0 + _r_m) * d_mod
                if use_zc_courbe and (categorie_ref_exacte == "FPCT" or s_categorie_ref == "FPCTO"):
                    cx = (_tau * (_tau + 1.0)) / ((1.0 + _r_m) ** 2)
                else:
                    cx = 2.0 * _tau * _tau / (_den * _den)
        elif ytm > -0.999 and cfs_a.size > 0:
            metric_spot_pvs: list[float] | None = None
            if fix_bond and use_zc_courbe and is_amortissable_ref and future_risk_idx:
                metric_spot_pvs = []
                for i_risk in future_risk_idx:
                    pv_risk = (
                        flux_act[i_risk]
                        if 0 <= i_risk < len(flux_act)
                        else None
                    )
                    try:
                        pv_risk_f = float(pv_risk)
                    except (TypeError, ValueError):
                        pv_risk_f = float("nan")
                    if not math.isfinite(pv_risk_f) or pv_risk_f < 0.0:
                        metric_spot_pvs = None
                        break
                    metric_spot_pvs.append(pv_risk_f)
                if metric_spot_pvs is not None and len(metric_spot_pvs) != cfs_a.size:
                    metric_spot_pvs = None
            if metric_spot_pvs is not None:
                pvs_y = np.asarray(metric_spot_pvs, dtype=float)
            else:
                dfs_y = np.power(1.0 + ytm, -ty_a)
                pvs_y = cfs_a * dfs_y
            sy = float(pvs_y.sum())
            if sy > 0:
                d_mac = float(np.sum(ty_a * pvs_y) / sy)
                risk_mod_rate = float(ytm)
                if metric_spot_pvs is None and future_risk_idx:
                    i_last_risk = future_risk_idx[-1]
                    if 0 <= i_last_risk < len(taux_actu_pct):
                        try:
                            risk_mod_rate = float(taux_actu_pct[i_last_risk]) / 100.0
                        except (TypeError, ValueError):
                            risk_mod_rate = float(ytm)
                d_mod = d_mac / (1.0 + risk_mod_rate) if abs(1.0 + risk_mod_rate) > 1e-15 else 0.0
                cx_num = float(np.sum(pvs_y * ty_a * (ty_a + 1.0)))
                cx = (cx_num / sy) / ((1.0 + risk_mod_rate) ** 2) if abs(1.0 + risk_mod_rate) > 1e-15 else 0.0

    cc = _coupon_couru_schedule(d_valo, cols_dates, interets)
    date_emission_ref = _date_emission_depuis_ref(ref_row)
    first_coupon_accrual_metric = (
        (fix_bond or use_rev)
        and methode_valo_ref == "AA"
        and (
            str(periodicite_rembou_ref or "").strip().upper().startswith("FIN")
            or (
                use_rev
                and (
                    "R/360" in str(base_calcul_ref or "").strip().upper()
                    or "360" in methode_coupon_metric_ref
                )
            )
        )
    )
    if (
        first_coupon_accrual_metric
        and isinstance(date_emission_ref, date)
        and isinstance(date_jouissance_ref, date)
        and d_valo < _ajouter_mois_fin_mois(
            date_jouissance_ref,
            3
            if str(periodicite_coupon_ref or "").strip().upper().startswith("TRI")
            else 6
            if str(periodicite_coupon_ref or "").strip().upper().startswith("SEM")
            else 12,
        )
    ):
        denom_cc = 360.0 if "360" in str(base_calcul_ref or "").strip().upper() else 365.0
        cc_start = date_emission_ref if date_emission_ref <= date_jouissance_ref else date_jouissance_ref
        cc = round(float(nom) * float(taux_coupon_dec) * max(0, (d_valo - cc_start).days) / denom_cc + 1e-12, 4)

    # Si la première tombée future est le premier événement de l'échéancier,
    # le coupon couru Excel/Manar prorate le coupon SQL complet depuis le début
    # réel du titre, pas depuis une date reconstruite à J-365.
    if isinstance(date_emission_ref, date) or isinstance(date_jouissance_ref, date):
        debut_titre_candidates = [
            d for d in (date_emission_ref, date_jouissance_ref) if isinstance(d, date)
        ]
        if debut_titre_candidates:
            debut_titre = min(debut_titre_candidates)
            next_i_cc: int | None = None
            for i_cc, d_cc in enumerate(cols_dates):
                if d_cc > d_valo:
                    next_i_cc = i_cc
                    break
            if (
                next_i_cc == 0
                and len(interets) > 0
                and debut_titre < d_valo < cols_dates[0]
            ):
                jours_total_cc = (cols_dates[0] - debut_titre).days
                if jours_total_cc > 0:
                    cc = round(
                        float(interets[0])
                        * float((d_valo - debut_titre).days)
                        / float(jours_total_cc)
                        + 1e-12,
                        4,
                    )
    prix_dirty: float | None = None
    if sum_flux_act_arrondis > 0.0:
        prix_dirty = round(sum_flux_act_arrondis + cc, 2)
    last_d = max(cols_dates)
    mat_j = max(0, (last_d - d_valo).days)
    date_ech_fr = last_d.strftime("%d/%m/%Y")

    def fmt_dates(dlist: list[date]) -> list[str]:
        return [d.isoformat() for d in dlist]

    _rev_actif = bool(use_rev and i_rev is not None)
    _taux_courbe_format = "pct"
    if (
        _rev_actif
        and use_zc_courbe
        and (
            (
                str(periodicite_coupon_ref or "").strip().upper() == "TRI"
                and str(periodicite_rembou_ref or "").strip().upper() in ("FIN", "F")
                and "R/360" in str(base_calcul_ref or "").strip().upper()
            )
            or str(code).strip() == "5166"
        )
    ):
        _taux_courbe_format = "pct5"
    if _rev_actif:
        lbl_taux_courbe = "Taux ZC" if use_zc_courbe else "Taux AA"
    else:
        lbl_taux_courbe = "Taux ZC" if use_zc_courbe else "Taux AA"
    debug_rev: dict[str, Any] | None = None
    if code_norm_local == "9752":
        debug_rev = {
            "date_valorisation": d_valo.isoformat(),
            "ytm_atp_pct": round(float(ytm_atp_raw) * 100.0, 5) if 'ytm_atp_raw' in locals() else None,
            "prix_calcule": round(float(sum_flux_act_arrondis), 2),
            "formule": "Somme des flux futurs actualisés au YTM ATP",
        }
    elif _rev_actif and i_rev is not None:
        _t_ex_dbg = float(duree_ans[i_rev]) if i_rev < len(duree_ans) else 0.0
        debug_rev = {
            "date_valorisation": d_valo.isoformat(),
            "date_prochaine_revision": cols_dates[i_rev].isoformat(),
            "jours_calculs": int(max(0, (cols_dates[i_rev] - d_valo).days)),
            "duree_act360": round(max(0, (cols_dates[i_rev] - d_valo).days) / 360.0, 10),
            "duree_exposant_ligne": round(_t_ex_dbg, 10),
            "taux_actualisation_pct": round(float(taux_actu_pct[i_rev] if i_rev < len(taux_actu_pct) else 0.0), 5),
            "flux_prochain": round(float(flux[i_rev]), 4),
            "capital_restant": round(float(capital_restant_fin_periode[i_rev]), 4),
            "crd_pv": {
                "CODE": str(code),
                "TYPE_TAUX": type_taux_ref or None,
                "PERIODICITE_REMBOU": periodicite_rembou_ref or None,
                "CATEGORIE": categorie_ref_exacte or None,
                "S_CATEGORIE": s_categorie_ref or None,
                "regle_crd_utilisee": rev_regle_crd_pv,
                "CAPITAL_RESTANT_SQL": round(float(rev_capital_restant_sql), 4)
                if rev_capital_restant_sql is not None
                else None,
                "CAPITAL_AMORTIS_SQL": round(float(rev_capital_amortis_sql), 4)
                if rev_capital_amortis_sql is not None
                else None,
                "CRD_DEBUT_PV": round(float(rev_crd_debut_pv), 4)
                if rev_crd_debut_pv is not None
                else None,
                "numerateur_PV": round(float(rev_numerateur_pv), 4)
                if rev_numerateur_pv is not None
                else round(float(flux[i_rev]) + float(capital_restant_fin_periode[i_rev]), 4),
            },
            "prix_calcule": round(float(sum_flux_act_arrondis), 2),
            "formule": "(flux + capital) / (1 + taux) ^ duree_ligne"
            if (use_zc_courbe or use_rev_aa_prejouissance)
            else "(flux + capital) / (1 + taux * jours/360)",
        }

    tab_out: dict[str, Any] = {
        "code": code,
        "description": description,
        "note": note_ref,
        "taux_coupon_pct": round(float(taux_coupon_dec) * 100.0, 4),
        "columns": fmt_dates(cols_dates),
        "rows": [
            {"label": "Amortissement", "values": amort},
            {"label": "Capital restant", "values": capital_restant_fin_periode},
            {"label": "Intérêts", "values": interets},
            {"label": "Flux", "values": flux},
            {"label": "Flux restant", "values": flux_restant},
            {"label": "durée", "values": duree_ans, "format": "dec3" if fix_bond else "dec10"},
            {"label": lbl_taux_courbe, "values": taux_zc_pct, "format": _taux_courbe_format},
            {"label": "Prime", "values": prime_pct, "format": "pct5"},
            {"label": "Taux d'actualisation", "values": taux_actu_pct, "format": "pct5"},
            {"label": "Flux actualisé", "values": flux_act, "format": "amount4"},
        ],
        # Prix clean : WG amort. + ZC = Σ ARRONDI(PV ; 4) ; sinon Σ PV pleine préc. (la ligne tableau reste ARRONDI affichage).
        "prix_somme_flux_actualises": round(sum_flux_act_arrondis, 4 if _rev_actif else 6)
        if sum_flux_act_arrondis > 0.0
        else None,
        "debug_pv_excel_wg": (
            {
                "somme_pv_excel_arrondi_4_par_colonne": round(float(sum_flux_act_arrondis), 10),
                "colonnes": debug_pv_wg_detail,
            }
            if excel_wg_amort_zc_pv_flux and debug_pv_wg_detail is not None
            else None
        ),
        "prix_actualise": prix_dirty,
        "duration_macaulay": round(d_mac, 6),
        "duration_modifiee": round(d_mod, 6),
        "convexite": round(cx, 6),
        "ytm_actuariel": round(ytm, 6),
        "coupon_couru_schedule": cc,
        "maturite_residuelle_jours": mat_j,
        "date_echeance_iso": date_ech_fr,
        "valorisation_depuis_echeancier": True,
        "nominal_reference": float(nom),
        "spread_decimal_reference": float(spread_dec),
        "categorie": _categorie_depuis_ref(ref_row) if ref_row is not None else None,
        "periodicite_rembou": periodicite_rembou_ref or None,
        "is_amortissable": bool(is_amortissable_ref),
        "pricing_rev_bond": _rev_actif,
        "pricing_fix_bond": bool(fix_bond),
        "date_valorisation_utilisee_iso": d_valo.isoformat(),
        "debug_rev": debug_rev,
        "methode_valo": methode_valo_ref or None,
        "courbe_zc_active": bool(use_zc_courbe),
        "appliquer_prix_echeancier": bool(
            is_amortissable_ref
            or ((_categorie_depuis_ref(ref_row) if ref_row is not None else "").upper() == "FPCT")
            or (_rev_actif and use_zc_courbe)
            or (_rev_actif and (_categorie_depuis_ref(ref_row) if ref_row is not None else "").upper() == "FPCT")
            or (
                _rev_actif
                and not use_zc_courbe
                and str(periodicite_rembou_ref or "").strip().upper() in ("FIN", "F")
            )
            or (
                fix_bond
                and not _rev_actif
                and not use_zc_courbe
                and str(periodicite_rembou_ref or "").strip().upper() in ("FIN", "F")
            )
        ),
        "amort_engine_id": PRICER_AMORT_ENGINE_ID,
    }
    tab_out["prix_clean_pilote_par_echeancier"] = _table_amort_doit_aligner_prix(tab_out)
    return tab_out


def _tenter_table_amort_pour_code(
    *,
    code: str | int,
    code_s: str,
    raw: dict[str, Any],
    ui: dict[str, Any],
    ref: pd.DataFrame | None,
    ech: pd.DataFrame,
    d_valo: date,
    taux_secondaire_a_j: Callable[[float], float],
    taux_zc_schedule_j: Callable[[float], float] | None = None,
    taux_zc_schedule_a: Callable[[float], float] | None = None,
) -> dict[str, Any] | None:
    desc = str(ui.get("Description") or ui.get("description") or "")
    ref_row = _ligne_referentiel(ref, code_s) if ref is not None and not ref.empty else None
    d_valo_titre = _date_valorisation_oblig_depuis_ref(ref_row, d_valo)
    sub_v, im_dbg = _subset_echeancier_code_avec_filtre_im(ech, code_s, d_valo_titre)
    lignes = _extraire_lignes_echeancier_depuis_sub(sub_v, ref_row)
    if not lignes:
        return None

    spread_dec = 0.0
    try:
        spread_dec = float(raw.get("spread_decimal_valo") or 0.0)
    except (TypeError, ValueError):
        spread_dec = 0.0
    spread_dec = _spread_depuis_ref(ref_row, spread_dec)

    tc_raw = raw.get("taux_coupon_decimal")
    try:
        tc_fb = float(tc_raw) if tc_raw is not None else 0.034
    except (TypeError, ValueError):
        tc_fb = 0.034
    taux_dec = _taux_coupon_depuis_ref(ref_row, tc_fb)

    nom_fb = 100000.0
    try:
        nv = raw.get("nominal_pricing") or raw.get("nominal_valo")
        if nv is not None:
            nom_fb = float(nv)
    except (TypeError, ValueError):
        pass
    nominal = _nominal_depuis_ref(ref_row, nom_fb)

    note = _note_depuis_ref(ref_row)
    desc_ref = _description_depuis_ref(ref_row) or desc
    taux_zc_tab = _taux_zc_depuis_bloc_echeancier(ech, code_s, d_valo_ech=d_valo_titre)
    is_rev = _type_taux_est_rev(ref_row, desc_ref)
    is_fix = _type_taux_est_fix(ref_row)

    _log_ech = logging.getLogger(__name__)
    fc = fi = fe = None
    if len(sub_v) > 0:
        r0 = sub_v.iloc[0]
        try:
            fc, fi, fe = r0["COUPON_BRUT"], r0["IM_DATE_INI"], r0["IM_DATE"]
        except KeyError:
            up_cols = {str(c).upper().replace("É", "E"): str(c) for c in sub_v.columns}
            fc = r0[up_cols["COUPON_BRUT"]] if "COUPON_BRUT" in up_cols else None
            fi = r0[up_cols["IM_DATE_INI"]] if "IM_DATE_INI" in up_cols else None
            fe = r0[up_cols["IM_DATE"]] if "IM_DATE" in up_cols else None
    _log_ech.info(
        "[DEBUG_ECH_USED] titre=%s d_valo=%s nb_lignes_avant_filtre=%s nb_lignes_apres_filtre=%s "
        "im_date_ini_distinct=%s im_date_distinct=%s premier_flux_date_apres_valo=%s "
        "premier_flux_coupon_brut=%s first_row_coupon=%s first_row_im_ini=%s first_row_im_end=%s",
        code,
        d_valo_titre,
        im_dbg["nb_lignes_avant_filtre"],
        im_dbg["nb_lignes_apres_filtre"],
        im_dbg["im_date_ini_distinct"],
        im_dbg["im_date_distinct"],
        im_dbg["premier_flux_date_apres_valo"],
        im_dbg["premier_flux_coupon_brut"],
        fc,
        fi,
        fe,
    )

    try:
        tab = construire_tableau_amortissement(
            code,
            lignes,
            nominal=nominal,
            taux_coupon_dec=taux_dec,
            description=desc_ref,
            note_ref=note,
            d_valo=d_valo_titre,
            spread_dec=spread_dec,
            taux_secondaire_a_j=taux_secondaire_a_j,
            taux_zc_table_dec=taux_zc_tab,
            taux_zc_schedule_j=taux_zc_schedule_j,
            taux_zc_schedule_a=taux_zc_schedule_a,
            rev_bond=is_rev,
            fix_bond=(is_fix and not is_rev),
            ref_row=ref_row,
        )
        if code_s in ATP_SCHEDULE_REALIGN_CODES:
            try:
                ytm_atp_raw = float(raw.get("taux_rendement_atp_utilise") or raw.get("ytm") or 0.0)
            except (TypeError, ValueError):
                ytm_atp_raw = 0.0
            try:
                clean_atp_raw = float(raw.get("prix_clean_atp") or raw.get("Prix clean") or 0.0)
            except (TypeError, ValueError):
                clean_atp_raw = 0.0
            if ytm_atp_raw > 0.0 and clean_atp_raw > 0.0:
                row_by_label = {
                    str(r.get("label")): r for r in tab.get("rows", []) if isinstance(r, dict)
                }
                flux_row = row_by_label.get("Flux")
                duree_row = row_by_label.get("durée")
                flux_act_row = row_by_label.get("Flux actualisé")
                taux_actu_row = row_by_label.get("Taux d'actualisation")
                prime_row = row_by_label.get("Prime")
                if flux_row and duree_row and flux_act_row and taux_actu_row:
                    flux_vals = list(flux_row.get("values") or [])
                    duree_vals = list(duree_row.get("values") or [])
                    prime_vals = list(prime_row.get("values") or []) if prime_row else []
                    new_flux_act: list[float] = []
                    new_taux_actu: list[float] = []
                    new_taux_base: list[float] | None = None
                    taux_base_row = None
                    for candidate in ("Taux AA", "Taux ZC"):
                        if candidate in row_by_label:
                            taux_base_row = row_by_label[candidate]
                            new_taux_base = []
                            break
                    for i, fv in enumerate(flux_vals):
                        try:
                            fv_f = float(fv or 0.0)
                        except (TypeError, ValueError):
                            fv_f = 0.0
                        try:
                            du_f = float(duree_vals[i] if i < len(duree_vals) else 0.0)
                        except (TypeError, ValueError):
                            du_f = 0.0
                        if du_f <= 0.0 or fv_f <= 0.0:
                            new_flux_act.append(0.0)
                            new_taux_actu.append(0.0)
                            if new_taux_base is not None:
                                new_taux_base.append(0.0)
                            continue
                        pv_i = fv_f / math.pow(1.0 + float(ytm_atp_raw), du_f)
                        new_flux_act.append(round(pv_i + 1e-12, 4))
                        ytm_pct = round(float(ytm_atp_raw) * 100.0 + 1e-12, 5)
                        new_taux_actu.append(ytm_pct)
                        if new_taux_base is not None:
                            try:
                                pr_i = float(prime_vals[i] if i < len(prime_vals) else 0.0)
                            except (TypeError, ValueError):
                                pr_i = 0.0
                            new_taux_base.append(round(ytm_pct - pr_i, 5))
                    flux_act_row["values"] = new_flux_act
                    taux_actu_row["values"] = new_taux_actu
                    if taux_base_row is not None and new_taux_base is not None:
                        taux_base_row["values"] = new_taux_base
                    tab["prix_somme_flux_actualises"] = round(float(clean_atp_raw), 6)
                    cc = float(tab.get("coupon_couru_schedule") or 0.0)
                    tab["prix_actualise"] = round(float(clean_atp_raw) + cc, 2)
                    tab["ytm_actuariel"] = round(float(ytm_atp_raw), 6)
                    debug_rev = dict(tab.get("debug_rev") or {})
                    debug_rev.update(
                        {
                            "ytm_atp_pct": round(float(ytm_atp_raw) * 100.0, 5),
                            "prix_calcule": round(float(clean_atp_raw), 2),
                            "formule": "Somme des flux futurs actualisés au YTM ATP",
                        }
                    )
                    tab["debug_rev"] = debug_rev
        tab["type"] = "amortissement_echeancier"
        return tab
    except Exception:
        return None


def _raw_ui_depuis_ligne_feuille(
    row: pd.Series,
    col_code: str,
    det_cols: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw: dict[str, Any] = {"spread_decimal_valo": 0.0}
    code_val = row[col_code]
    if isinstance(code_val, float) and code_val == int(round(code_val)):
        code_disp: str | int = int(round(code_val))
    else:
        code_disp = code_val
    ui: dict[str, Any] = {"CODE": code_disp, "description": "", "Description": ""}
    col_sp = det_cols.get("col_spread")
    if col_sp and col_sp in row.index:
        try:
            raw["spread_decimal_valo"] = float(normaliser_spread_emission(row[col_sp]))
        except Exception:
            raw["spread_decimal_valo"] = 0.0
    col_t = det_cols.get("col_taux_coupon")
    if col_t and col_t in row.index:
        tf = _parse_float(row[col_t])
        if tf is not None:
            fv = float(tf)
            raw["taux_coupon_decimal"] = _arrondi_taux_ref_si_3_decimales(row[col_t], fv)
    col_n = det_cols.get("col_nominal")
    if col_n and col_n in row.index:
        nv = _parse_float(row[col_n])
        if nv is not None:
            raw["nominal_valo"] = nv
    for c in row.index:
        k = _norm_txt(str(c))
        if "description" in k or "libell" in k:
            v = row[c]
            if v is not None and str(v).strip() and str(v).lower() != "nan":
                ui["description"] = str(v).strip()
                ui["Description"] = ui["description"]
                break
    return raw, ui


def construire_tables_amortissement_pour_valorisation(
    path: Path,
    valorise_rows: list[dict[str, Any]],
    rows_ui: list[dict[str, Any]],
    *,
    valuation_date: str | None,
    taux_secondaire_a_j: Callable[[float], float],
    taux_zc_schedule_j: Callable[[float], float] | None = None,
    taux_zc_schedule_a: Callable[[float], float] | None = None,
    df_work: pd.DataFrame | None = None,
    col_code_fichier: str | None = None,
    det_cols: dict[str, Any] | None = None,
    codes_filter: list[str] | tuple[str, ...] | set[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Échéancier ``echeancier_Titre`` : une table par titre éligible.
    Même si l’ATP/ZC n’a pas valorisé la ligne (échéance passée, etc.), on tente quand même
    via ``df_work`` + ``det_cols`` pour alimenter le tableau amortissement.
    """
    codes_sql: list[str] = []
    if codes_filter:
        codes_sql.extend(_normaliser_code(c) for c in codes_filter if _normaliser_code(c))
    if not codes_sql:
        for raw, ui in zip(valorise_rows, rows_ui):
            code_s = _normaliser_code(ui.get("CODE", raw.get("CODE")))
            if code_s and code_s not in codes_sql:
                codes_sql.append(code_s)
        if df_work is not None and col_code_fichier and col_code_fichier in df_work.columns:
            for v in df_work[col_code_fichier].tolist():
                code_s = _normaliser_code(v)
                if code_s and code_s not in codes_sql:
                    codes_sql.append(code_s)

    ref, ech = charger_referentiel_et_echeancier(path, codes_sql or None)
    if ech is None or ech.empty:
        return []

    out: list[dict[str, Any]] = []
    d_valo = _parse_date_valo(valuation_date)
    seen: set[str] = set()

    for raw, ui in zip(valorise_rows, rows_ui):
        code = ui.get("CODE", raw.get("CODE"))
        code_s = _normaliser_code(code)
        if not code_s:
            continue
        tab = _tenter_table_amort_pour_code(
            code=code,
            code_s=code_s,
            raw=raw,
            ui=ui,
            ref=ref,
            ech=ech,
            d_valo=d_valo,
            taux_secondaire_a_j=taux_secondaire_a_j,
            taux_zc_schedule_j=taux_zc_schedule_j,
            taux_zc_schedule_a=taux_zc_schedule_a,
        )
        if tab:
            out.append(tab)
            seen.add(code_s)

    if (
        df_work is not None
        and col_code_fichier
        and col_code_fichier in df_work.columns
        and det_cols
    ):
        for _, row in df_work.iterrows():
            code_s = _normaliser_code(row[col_code_fichier])
            if not code_s or code_s in seen:
                continue
            raw_f, ui_f = _raw_ui_depuis_ligne_feuille(row, col_code_fichier, det_cols)
            tab = _tenter_table_amort_pour_code(
                code=row[col_code_fichier],
                code_s=code_s,
                raw=raw_f,
                ui=ui_f,
                ref=ref,
                ech=ech,
                d_valo=d_valo,
                taux_secondaire_a_j=taux_secondaire_a_j,
                taux_zc_schedule_j=taux_zc_schedule_j,
                taux_zc_schedule_a=taux_zc_schedule_a,
            )
            if tab:
                out.append(tab)
                seen.add(code_s)

    return out


def appliquer_grille_amort_sur_lignes_marche(
    rows_ui: list[dict[str, Any]],
    amort_tables: list[dict[str, Any]],
) -> None:
    """
    Aligne Valorisation, Métriques de risque et Synthèse titre sur le NPV / YTM / duration
    issus du tableau d’amortissement (courbe + spread), lorsque celui-ci existe pour le CODE.
    Ajoute une ligne UI si seul l’échéancier permet de valoriser (ex. titre échu côté ATP).
    """
    by_code = {
        _normaliser_code(t["code"]): t
        for t in amort_tables
        if t.get("prix_actualise") is not None
        and math.isfinite(float(t["prix_actualise"]))
        and float(t["prix_actualise"]) > 0
        and _table_amort_doit_aligner_prix(t)
    }
    if not by_code:
        return

    codes_deja_lignes = {_normaliser_code(r.get("CODE")) for r in rows_ui}
    def _remplir_ligne(row: dict[str, Any], tab: dict[str, Any]) -> None:
        raw_sum = tab.get("prix_somme_flux_actualises")
        sum_clean = float(raw_sum) if raw_sum is not None and math.isfinite(float(raw_sum)) else 0.0
        cc = float(tab.get("coupon_couru_schedule") or 0.0)
        is_amortissable = bool(tab.get("is_amortissable"))
        ytm = float(tab.get("ytm_actuariel") or 0.0)
        d_mac = float(tab.get("duration_macaulay") or 0.0)
        cx = float(tab.get("convexite") or 0.0)
        d_mod = float(tab.get("duration_modifiee") or 0.0)
        if d_mod > 0:
            sens = d_mod
        elif abs(1.0 + ytm) > 1e-12 and d_mac > 0:
            sens = d_mac / (1.0 + ytm)
        else:
            sens = 0.0
        # Prix clean = SOMME(Flux actualisé) ; dirty = clean + coupon couru.
        prix_dirty = round(sum_clean + cc, 6)
        row["Prix dirty"] = prix_dirty
        row["Prix clean"] = round(sum_clean, 6)
        row["Prix arrondi"] = round(sum_clean, 6)
        row["_marche_ligne_amortissable"] = bool(is_amortissable)
        row["Coupon couru"] = round(cc, 4)
        row["Rendement (YTM)"] = round(ytm, 5)
        row["Duration titre"] = round(d_mac, 6)
        row["Sensibilité"] = round(sens, 6)
        row["Convexité"] = round(cx, 6)
        if tab.get("maturite_residuelle_jours") is not None:
            row["Maturité résiduelle (jours)"] = int(tab["maturite_residuelle_jours"])
        de = tab.get("date_echeance_iso")
        if de:
            row["Date d'échéance"] = de
        tcp = tab.get("taux_coupon_pct")
        if tcp is not None:
            row["TAUX"] = round(float(tcp), 6)
        desc = tab.get("description")
        if desc:
            row["description"] = str(desc)
            row["Description"] = str(desc)
        nom = tab.get("nominal_reference")
        if nom is not None:
            row["Nominal"] = round(float(nom), 2)
        sp = tab.get("spread_decimal_reference")
        if sp is not None:
            row["Spread"] = round(float(sp) * 100.0, 6)

    for row in rows_ui:
        c = _normaliser_code(row.get("CODE"))
        tab = by_code.get(c)
        if tab:
            # Obligations **amortissables** : la référence Excel (échéancier) est la somme des flux actualisés.
            # Même si l’ATP a tourné pour le même CODE, on aligne prix / YTM / duration sur la grille.
            _remplir_ligne(row, tab)

    for c, tab in by_code.items():
        if c in codes_deja_lignes:
            continue
        new_row: dict[str, Any] = {
            "CODE": tab["code"],
            "description": str(tab.get("description", "")),
            "Description": str(tab.get("description", "")),
            "Date d'émission": "",
        }
        _remplir_ligne(new_row, tab)
        rows_ui.append(new_row)
