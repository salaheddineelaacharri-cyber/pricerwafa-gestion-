"""
Module autonome de valorisation obligataire (portage VBA -> Python).

Ce module implémente les fonctions financières demandées, sans dépendance
numpy/pandas, avec logique métier alignée sur les formules VBA.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import List, Optional

try:
    from dateutil.relativedelta import relativedelta
except Exception:  # pragma: no cover - fallback si dateutil indisponible
    relativedelta = None  # type: ignore[assignment]


def ajouter_annees(d: date, n: int) -> date:
    """
    Ajoute n années à une date.

    Cas spécial VBA: 29/02 -> 28/02 si l'année cible n'est pas bissextile.
    """
    if relativedelta is not None:
        try:
            return d + relativedelta(years=n)
        except ValueError:
            # Défense supplémentaire si l'implémentation locale lève une erreur.
            if d.month == 2 and d.day == 29:
                return date(d.year + n, 2, 28)
            raise

    # Fallback manuel sans python-dateutil.
    year = d.year + n
    month = d.month
    day = d.day
    if month == 2 and day == 29:
        day = 28
    while True:
        try:
            return date(year, month, day)
        except ValueError:
            day -= 1
            if day <= 0:
                raise


def _jours_annee(d: date) -> int:
    """Nombre de jours de l'année de d."""
    year = d.year
    bissextile = (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)
    return 366 if bissextile else 365


def _compter_flux_restants(date_liquidation: date, date_echeance: date) -> int:
    """
    Compte le nombre de flux annuels restants (logique VBA MLT):
    en soustrayant 1 an à la date d'échéance jusqu'à <= date_liquidation.
    """
    if date_echeance <= date_liquidation:
        return 0
    d = date_echeance
    n = 0
    while d > date_liquidation:
        n += 1
        d = ajouter_annees(d, -1)
    return n


def coupon_couru(
    date_liquidation: date,
    date_emission: date,
    date_echeance: date,
    taux: float,
    nominal: float,
    premier_j_inclus: str,
    periodicite_cp: str,
    periodicite_cap: str,
    maturite: int,
    mode_valorisation: str,
    base: str,
) -> float:
    """
    Calcule le coupon couru selon la logique VBA.

    - CT: maturite in [13, 26, 52]
    - MLT: dernière date anniversaire <= date_liquidation
    """
    _ = periodicite_cp, mode_valorisation  # paramètres conservés pour compatibilité signature VBA.
    inclus = 1 if str(premier_j_inclus).strip().upper() == "O" else 0
    cap = str(periodicite_cap).strip().upper()

    if maturite in (13, 26, 52):
        nbr_jours = (date_liquidation - date_emission).days + inclus
        return (nbr_jours / 360.0) * taux * nominal

    # MLT
    d = date_echeance
    while d > date_liquidation:
        d = ajouter_annees(d, -1)
    nbr_jours = (date_liquidation - d).days + inclus

    nbr_flux = _compter_flux_restants(date_liquidation, date_echeance)
    if cap == "F":
        coupon = nominal * taux
    else:
        coupon = round(
            round(nominal - round(nominal / maturite, 2) * (maturite - nbr_flux), 2) * taux,
            2,
        )

    n_base = _jours_annee(date_liquidation) if str(base).strip().upper() == "V" else 365
    return coupon * nbr_jours / n_base


def prix_titre(
    date_liquidation: date,
    date_emission: date,
    date_echeance: date,
    taux: float,
    nominal: float,
    premier_j_inclus: str,
    mode_valorisation: str,
    periodicite_cp: str,
    periodicite_cap: str,
    maturite: int,
    rendement: float,
    base: str,
) -> float:
    """
    Prix d'un titre selon logique VBA (M, L, A).
    """
    _ = periodicite_cp
    if date_echeance <= date_liquidation:
        return 0.0

    mode = str(mode_valorisation).strip().upper()
    cap = str(periodicite_cap).strip().upper()
    inclus = 1 if str(premier_j_inclus).strip().upper() == "O" else 0

    # ETAPE 1: échéancier des flux
    flux: List[float] = []
    echeancier: List[date] = []

    if maturite in (13, 26, 52):
        flux_ct = round(nominal * (1.0 + taux * (date_echeance - date_emission).days / 360.0), 2)
        flux = [flux_ct]
        echeancier = [date_echeance]
        nbr_flux = 1
    else:
        nbr_flux = _compter_flux_restants(date_liquidation, date_echeance)
        for i in range(1, nbr_flux + 1):
            d_i = ajouter_annees(date_echeance, i - nbr_flux)
            echeancier.append(d_i)
            if cap == "F":
                if i == nbr_flux:
                    f_i = round(nominal * (1.0 + taux), 2)
                else:
                    f_i = round(nominal * taux, 2)
            else:
                f_i = round((nominal / maturite) * (1.0 + (nbr_flux - i + 1) * taux), 2)
            flux.append(f_i)

    # ETAPE 2: coupon couru
    cc = coupon_couru(
        date_liquidation=date_liquidation,
        date_emission=date_emission,
        date_echeance=date_echeance,
        taux=taux,
        nominal=nominal,
        premier_j_inclus=premier_j_inclus,
        periodicite_cp=periodicite_cp,
        periodicite_cap=periodicite_cap,
        maturite=maturite,
        mode_valorisation=mode_valorisation,
        base=base,
    )

    # ETAPE 3: prix selon mode
    if mode == "M":
        return flux[0] / (1.0 + rendement * (echeancier[0] - date_liquidation).days / 360.0)

    if mode == "L":
        if cap == "F":
            return nominal + cc
        return round(nominal - round(nominal / maturite, 2) * (maturite - nbr_flux), 2) + cc

    if mode == "A":
        p = 0.0
        # dénominateur de fraction d'année jusqu'au prochain coupon
        denom = (echeancier[0] - ajouter_annees(echeancier[0], -1)).days
        if denom == 0:
            return 0.0
        for i in range(nbr_flux):
            di = (i + (echeancier[0] - date_liquidation).days - inclus) / denom
            p += flux[i] / ((1.0 + rendement) ** di)
        return p

    raise ValueError(f"Mode de valorisation inconnu: {mode}")


def rendement_titre(
    date_liquidation: date,
    date_emission: date,
    date_echeance: date,
    taux: float,
    nominal: float,
    premier_j_inclus: str,
    mode_valorisation: str,
    periodicite_cp: str,
    periodicite_cap: str,
    maturite: int,
    base: str,
    prix: float,
) -> float:
    """
    Calcule le rendement par dichotomie sur [0, 0.2], erreur 1e-9.
    """
    a = 0.0
    b = 0.2
    erreur = 1e-9

    pa = prix_titre(
        date_liquidation,
        date_emission,
        date_echeance,
        taux,
        nominal,
        premier_j_inclus,
        mode_valorisation,
        periodicite_cp,
        periodicite_cap,
        maturite,
        a,
        base,
    ) - prix
    pb = prix_titre(
        date_liquidation,
        date_emission,
        date_echeance,
        taux,
        nominal,
        premier_j_inclus,
        mode_valorisation,
        periodicite_cp,
        periodicite_cap,
        maturite,
        b,
        base,
    ) - prix

    # Si l'encadrement ne change pas de signe, on garde la logique VBA sans élargir.
    while (b - a) > erreur:
        c = (a + b) / 2.0
        pc = prix_titre(
            date_liquidation,
            date_emission,
            date_echeance,
            taux,
            nominal,
            premier_j_inclus,
            mode_valorisation,
            periodicite_cp,
            periodicite_cap,
            maturite,
            c,
            base,
        ) - prix

        if pc == 0.0:
            a = c
            b = c
            break
        if pa * pc < 0:
            b = c
            pb = pc
        else:
            a = c
            pa = pc

    _ = pb  # conservé pour coller à l'algorithme VBA.
    return (a + b) / 2.0


def interpoler(maturites: list[float], taux: list[float], m: float) -> float:
    """
    Interpolation linéaire (logique VBA).
    - trouver i tel que m <= maturites[i]
    - si i == 0: taux[0]
    - sinon interpolation entre i-1 et i
    - si non trouvé: taux[0]
    """
    if not maturites or not taux or len(maturites) != len(taux):
        raise ValueError("maturites/taux invalides")

    for i, mat_i in enumerate(maturites):
        if m <= mat_i:
            if i == 0:
                return float(taux[0])
            m1, m2 = float(maturites[i - 1]), float(maturites[i])
            t1, t2 = float(taux[i - 1]), float(taux[i])
            if m2 == m1:
                return t2
            return (m - m1) * (t2 - t1) / (m2 - m1) + t1
    return float(taux[0])


def extrapoler(mat1: float, mat2: float, taux1: float, taux2: float, mat: float) -> float:
    """
    Extrapolation linéaire (logique VBA).
    """
    return (mat - mat2) * (taux2 - taux1) / (mat2 - mat1) + taux2


def prix_rev_lineaire_act360(
    flux_prochain: float,
    capital_restant_apres: float,
    taux_actualisation_decimal: float,
    jours_act360: int,
) -> float:
    """
    Prix clean obligation **révisable (REV)** : actualisation **linéaire** alignée Excel AWB.

    Même logique que la cellule **durée** du classeur : ``=(Date_colonne - $C$1) / 360``
    où ``$C$1`` est la **date de valorisation** (pas /365).

    - ``jours = (date_prochaine_révision - date_valorisation).days`` (écart calendaire Python,
      identique à Excel pour les dates du même calendrier).
    - ``t = jours / 360``
    - ``Prix = (Flux_prochain + Capital_restant_après) / (1 + r × t)`` avec **r** en décimal.

    Exemple : valorisation **26/03/2026**, révision **04/06/2026** → **70** jours →
    ``t = 70/360 = 0,19444444…``
    """
    t = max(0, int(jours_act360)) / 360.0
    num = float(flux_prochain) + float(capital_restant_apres)
    r = float(taux_actualisation_decimal)
    den = 1.0 + r * t
    if den <= 0.0 or not math.isfinite(den):
        return 0.0
    # Excel feuille REV: ARRONDI((Flux+Capital)/(1+TauxActu*Durée); 5)
    return round(num / den + 1e-12, 5)


def prix_rev_actualise_excel_puissance(
    flux_plus_capital: float,
    taux_actualisation_decimal: float,
    duree_exposant: float,
) -> float:
    """
    Actualisation type Excel **ZC** : ``(T + V) / (1 + Z) ^ Y``.

    - ``Z`` : taux d'actualisation **décimal** (ZC + prime, ex. 3,8720 % → 0,03872).
    - ``Y`` : exposant = **durée** affichée sur la ligne « durée » (même colonne que le flux).
    - Aligné sur la formule classeur : ``=(T8+V8)/(1+Z8)^Y8``.
    """
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


def taux_actualisation_rev_arrondi_excel(
    taux_aa_decimal: float,
    spread_decimal: float,
    ndigits_pct: int = 5,
) -> tuple[float, float]:
    """
    Retourne ``(taux_pct_arrondi_5_dec, taux_decimal)`` comme une somme **Taux AA + Prime**
    arrondie en **pourcentage** (ex. 2,83300 % → 0,02833).
    """
    aa = float(taux_aa_decimal)
    sp = float(spread_decimal)
    pct = round((aa + sp) * 100.0 + 1e-15, ndigits_pct)
    dec = pct / 100.0
    if not math.isfinite(dec):
        return 0.0, 0.0
    return float(pct), float(dec)


def _coerce_to_date(v: object) -> date | None:
    """Convertit une valeur (date/datetime/texte ISO) en ``date``."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s[:10]).date()
    except ValueError:
        return None


def get_facteur_periodicite(periodicite: str | None) -> float:
    """Facteur d’échelle de la durée REV : TRI → 0,25 ; SEM → 0,50 ; AN (ou défaut) → 1,00."""
    peri = str(periodicite or "").strip().upper()
    if "TRI" in peri:
        return 0.25
    if "SEM" in peri:
        return 0.50
    return 1.00


def calculer_duree_affichage_rev(
    date_valorisation: date,
    date_tombee: date,
    periodicite_coupon: str | None,
    base_calcul: str | None,
    *,
    code: str | int | None = None,
) -> float:
    """
    Durée REV : ``(jours_ecoules / jours_dans_periode) * facteur`` avec ``facteur`` selon ``PERIODICITE_COUPON``.

    - **TRI** → facteur **0,25** ; dénominateur = **jours réels du trimestre** (tombée − 3 mois), pour coller Excel
      (ex. ``(90/92)*0,25 = 0,2445652174``) — pas de dénominateur fixe 90 ici.
    - **SEM** → facteur **0,50** ; dénominateur **180** si ``R/360``, sinon jours réels du semestre.
    - **AN** (ou autre / vide) → facteur **1,00** ; dénominateur **360** si ``R/360``, sinon **365**.
    """
    if date_valorisation >= date_tombee:
        duree_arrondie = 0.0
        peri = str(periodicite_coupon or "").strip().upper()
        base = str(base_calcul or "").strip().upper()
        code_aff = str(code) if code is not None else "N/A"
        base_aff = base if base else "VIDE"
        print(
            f"[LOGIC] Code {code_aff}: Périodicité={peri or 'VIDE'}, "
            f"Base={base_aff} -> Valo>=Tombée => Durée={duree_arrondie:.10f}"
        )
        return duree_arrondie

    jours_ecoules = (date_tombee - date_valorisation).days
    peri = str(periodicite_coupon or "").strip().upper()
    base = str(base_calcul or "").strip().upper()
    use_r360 = "R/360" in base
    facteur = get_facteur_periodicite(periodicite_coupon)

    denom: float
    formule: str

    if "SEM" in peri:
        if use_r360:
            denom = 180.0
            formule = "SEM+R/360: (jours/180)*0.5"
        elif relativedelta is not None:
            d0 = date_tombee - relativedelta(months=6)
            denom = float(max(1, (date_tombee - d0).days))
            formule = "SEM+jours réels: (jours/j_sem)*0.5"
        else:
            denom = 182.0
            formule = "SEM+jours réels (fallback 182j): (jours/j_sem)*0.5"
    elif "TRI" in peri:
        if relativedelta is not None:
            d0 = date_tombee - relativedelta(months=3)
            denom = float(max(1, (date_tombee - d0).days))
            formule = "TRI: (jours/j_trim_reel)*0.25"
        else:
            denom = 91.0
            formule = "TRI: (jours/91)*0.25 (fallback sans dateutil)"
    else:
        # AN, vide ou autre libellé : facteur 1,00 (get_facteur_periodicite)
        denom = float(360 if use_r360 else 365)
        formule = f"AN/defaut: (jours/{int(denom)})*{facteur}"

    duree_arrondie = round((float(jours_ecoules) / denom) * float(facteur), 10)

    code_aff = str(code) if code is not None else "N/A"
    base_aff = base if base else "VIDE"
    print(
        f"[LOGIC] Code {code_aff}: Périodicité={peri or 'VIDE'}, Base={base_aff}, "
        f"Facteur={facteur}, Dénom.={denom:g} -> {formule}, Durée={duree_arrondie:.10f}"
    )
    return duree_arrondie


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
    """
    Prix REV avec formule Excel de vérité terrain :
    ``durée = (Date_colonne - $C$1) / 360``.

    - ``$C$1`` : ``date_valorisation`` (paramètre)
    - ``Date_colonne`` : prochaine date future de ``date_column`` strictement > ``date_valorisation``
    - ``jours = (date_revision - date_valorisation).days``
    - ``durée = jours / 360``
    - ``prix = (flux_prochain + capital_restant) / (1 + taux * durée)``
    """
    d_valo = _coerce_to_date(date_valorisation)
    if d_valo is None:
        raise ValueError("date_valorisation invalide")

    # Support dataframe pandas (to_dict("records")) ou liste de dicts.
    if hasattr(df_echeancier, "to_dict"):
        rows = df_echeancier.to_dict("records")
    else:
        rows = list(df_echeancier or [])

    if code is not None:
        code_s = str(code).strip()
        rows = [r for r in rows if str(r.get(code_column, "")).strip() == code_s]

    d_futures: list[date] = []
    for r in rows:
        d = _coerce_to_date(r.get(date_column))
        if d is not None and d > d_valo:
            d_futures.append(d)
    if not d_futures:
        raise ValueError("Aucune date de révision future trouvée dans l'échéancier.")

    next_revision_date = min(d_futures)
    jours = (next_revision_date - d_valo).days
    duree = jours / 360.0
    prix = prix_rev_lineaire_act360(
        flux_prochain=flux_prochain,
        capital_restant_apres=capital_restant,
        taux_actualisation_decimal=taux_actualisation_decimal,
        jours_act360=jours,
    )
    return prix, jours, duree, next_revision_date

