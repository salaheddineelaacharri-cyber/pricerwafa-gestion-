"""
Logique inspirée de la fonction VBA ``prix_ATP`` (DBT / obligations Maroc).

Vérification contre un classeur **PRICER WG** : ``python scripts/extraire_prix_atp_pricer_wg.py votre.xlsx --code 200792``
(placer le ``.xlsx`` à la racine du projet ou passer le chemin).

Principes repris :
- Pas de prix si date d’échéance ≤ date de liquidation (valorisation).
- **CT** : maturités 13 / 26 / 52 (semaines) → un seul flux à l’échéance, capitalisation simple type ACT/360.
- **MLT** : échéancier de flux jusqu’à l’échéance ; remboursement **in fine** (``periodicite_cap`` type F / FIN).
- **Coupon couru** : linéaire sur la période de coupon ; début d’accrual ``max(coupon_précédent, date_jouissance)``
  comme VBA ; la **date de jouissance** pour l’ATP est **calculée** à partir de l’émission et de l’échéance
  (même logique que la colonne « Commentaire / date de jouissance » du classeur WG : jj/mm de l’échéance,
  année d’émission ou suivante selon le cas), et non lue telle quelle depuis une colonne importée.
  Option ``taux_coupon_comme_vba`` pour ``nominal * taux`` (voir colonne ``ATP_COUPON_VBA``).
- **Modes** :
  - ``M`` : monétaire — actualisation du premier flux seul en ACT/360 ; prix clean quantifié en **6 décimales**
    (demi-supérieur, type ``ARRONDI`` Excel) pour éviter l’écart d’affichage du flottant.
  - ``L`` : linéaire — in fine : ``nominal + coupon_courru`` (valeur de rachat comptable type VBA).
  - ``A`` / ``AA`` : actuariel — comme VBA : ``di = (i-1) + stub`` et
    ``p += flux(i) / (1+rendement)^di`` (``rendement`` annuel en décimal, **sans** conversion
    taux/période), ``stub`` en fraction de période coupon calendaire.
"""

from __future__ import annotations

import calendar
import math
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import numpy as np

try:
    from dateutil.relativedelta import relativedelta
except ImportError:
    relativedelta = None  # type: ignore[misc, assignment]


# Même nombre de décimales que ``ARRONDI(...; 5)`` sur le rendement décimal dans le WG (cellule type T+R).
_RENDEMENT_DECIMALES_ARRONDI_EXCEL_M: int = 5


def _normalise_taux_coupon_annuel_wg_deux_dec_pct(taux_annuel_decimal: float) -> float:
    """
    Facial annuel en **décimal** (ex. ``0,056`` pour 5,60 %), aligné sur l’affichage Excel à **2** décimales %.

    Un fichier peut contenir ``5,599 %`` (``0,05599``) alors que le classeur affiche ``5,600 %`` : sans cette
    étape le coupon couru reste trop bas (ex. **5276,8658** au lieu de **5277,8082**).
    """
    t = float(taux_annuel_decimal)
    if not math.isfinite(t):
        return t
    pct = t * 100.0
    return round(pct + 1e-12, 2) / 100.0


def _pv_clean_mode_m_act_360(
    flux_montant_2dec: float,
    rendement_annuel_decimal: float,
    jours_jusqua_premier_flux: int,
) -> float:
    """
    Prix clean mode **M** : ``flux / (1 + R × j/360)`` avec ``flux`` déjà arrondi 2 déc. (comme VBA),
    puis arrondi **6 décimales** demi-supérieur (aligné ``ARRONDI`` Excel, pas le tie-to-even de ``round`` Python).

    Le rendement **R** est ré-appliqué en ``ARRONDI(R; 5)`` **dans** ce calcul : sans cela, un taux
    interpolé type ``0,022838`` (double IEEE) donne un clean **104678,511121** au lieu de **104678,470016**
    avec ``0,022840`` comme sur la feuille WG.
    """
    fm = Decimal(str(round(float(flux_montant_2dec) + 1e-12, 2)))
    y_dec = round(float(rendement_annuel_decimal) + 1e-15, _RENDEMENT_DECIMALES_ARRONDI_EXCEL_M)
    y = Decimal(str(y_dec))
    j = Decimal(int(jours_jusqua_premier_flux))
    disc = Decimal("1") + y * j / Decimal("360")
    if disc <= 0:
        return float(fm)
    pv = fm / disc
    q = Decimal("0.000001")
    return float(pv.quantize(q, rounding=ROUND_HALF_UP))


def _as_date(d: Any) -> date | None:
    if d is None:
        return None
    if isinstance(d, date) and not isinstance(d, datetime):
        return d
    if isinstance(d, datetime):
        return d.date()
    if hasattr(d, "date"):
        try:
            return d.date()  # pandas Timestamp
        except Exception:
            pass
    return None


def _date_calendaire_safe(year: int, month: int, day: int) -> date:
    """Dernier jour valide du mois si ``day`` dépasse la longueur du mois (ex. 29/02)."""
    dim = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, dim))


def date_jouissance_wg_depuis_emission_echeance(date_emission: date, date_echeance: date) -> date:
    """
    Date de jouissance **type classeur WG** (émission + échéance uniquement).

    - **Émission = échéance** (même jour calendaire) → jouissance = cette date.
    - **Échéance ≤ émission** (donnée incohérente) → repli sur l’émission.
    - Sinon : **jour et mois = échéance** ; première année à partir de l’année d’émission telle que
      la date soit **≥ émission** et **≤ échéance**. Si aucune date valide (ex. dépasse l’échéance),
      repli sur l’**émission**.

    Couvre les cas « maturité initiale < 1 an » (passage à l’année suivante si le jj/mm tombe avant
    l’émission dans l’année d’émission) et les obligations longues (jj/mm d’échéance, année d’émission).
    """
    if date_echeance <= date_emission:
        return date_emission
    mois, jour = date_echeance.month, date_echeance.day
    y = date_emission.year
    cand = _date_calendaire_safe(y, mois, jour)
    if cand < date_emission:
        y += 1
        cand = _date_calendaire_safe(y, mois, jour)
    if cand > date_echeance:
        return date_emission
    return cand


def _add_months(d: date, months: int) -> date:
    if relativedelta is not None:
        return (datetime.combine(d, datetime.min.time()) + relativedelta(months=months)).date()
    y, m = d.year, d.month + months
    while m > 12:
        m -= 12
        y += 1
    while m < 1:
        m += 12
        y -= 1
    last = min(d.day, _days_in_month(y, m))
    return date(y, m, last)


def _days_in_month(y: int, m: int) -> int:
    if m == 12:
        n = date(y + 1, 1, 1)
    else:
        n = date(y, m + 1, 1)
    return (n - date(y, m, 1)).days


def _step_coupon_backward(d: date, payments_per_year: int) -> date:
    if payments_per_year <= 1:
        return _add_months(d, -12)
    if payments_per_year == 2:
        return _add_months(d, -6)
    if payments_per_year == 4:
        return _add_months(d, -3)
    if payments_per_year == 12:
        return _add_months(d, -1)
    return _add_months(d, -int(12 / payments_per_year))


def _step_coupon_forward(d: date, payments_per_year: int) -> date:
    if payments_per_year <= 1:
        return _add_months(d, 12)
    if payments_per_year == 2:
        return _add_months(d, 6)
    if payments_per_year == 4:
        return _add_months(d, 3)
    if payments_per_year == 12:
        return _add_months(d, 1)
    return _add_months(d, int(12 / payments_per_year))


def _build_payment_dates_in_fine(
    d_liq: date,
    d_mat: date,
    payments_per_year: int,
) -> list[date]:
    """Dates de flux strictement après la liquidation jusqu’à l’échéance (coupon + principal final)."""
    if d_mat <= d_liq:
        return []
    dates: list[date] = []
    cur = d_mat
    guard = 0
    while cur > d_liq and guard < 500:
        dates.append(cur)
        nxt = _step_coupon_backward(cur, payments_per_year)
        if nxt >= cur:
            break
        cur = nxt
        guard += 1
    return list(reversed(dates))


def _coupon_nominal_period(nominal: float, taux_annuel: float, payments_per_year: int) -> float:
    return nominal * taux_annuel / float(payments_per_year)


def coupon_courru_atp(
    d_liq: date,
    d_last: date,
    d_next: date,
    nominal: float,
    taux_annuel: float,
    payments_per_year: int,
    *,
    premier_j_inclus: bool,
    d_jouissance: date | None = None,
    taux_coupon_comme_vba: bool = False,
) -> float:
    """
    Coupon couru linéaire sur la période courante (prochain coupon ``d_next``).

    Comme VBA ``coupon_courru(..., date_jouissance, ...)`` : le début d’accrual est
    ``max(date_coupon_précédent, date_jouissance)`` pour le premier coupon après jouissance.
    ``taux_coupon_comme_vba`` : montant de coupon période = ``nominal * taux`` (taux cellule
    déjà « par versement »), sinon facial **annuel** / ``payments_per_year``.
    """
    d_start = d_last
    if d_jouissance is not None and d_jouissance > d_start:
        d_start = d_jouissance
    if d_next <= d_start or d_liq >= d_next:
        return 0.0
    if taux_coupon_comme_vba:
        cpn = float(nominal) * float(taux_annuel)
    else:
        cpn = _coupon_nominal_period(nominal, taux_annuel, payments_per_year)
    den = (d_next - d_start).days
    if den <= 0:
        return 0.0
    acc = (d_liq - d_start).days
    if premier_j_inclus:
        acc += 1
    acc = max(0, min(acc, den))
    return round(cpn * acc / den + 1e-12, 4)


def _ajouter_periodes_coupon(d: date, n_steps: int, payments_per_year: int) -> date:
    """Comme VBA ``ajouter(d, n_steps)`` : nombre entier de périodes (positif = avant, négatif = arrière selon signe Excel). Ici ``n_steps > 0`` = en avant, ``< 0`` = en arrière (aligné sur la boucle MLT VBA)."""
    cur = d
    if n_steps == 0:
        return cur
    if n_steps > 0:
        for _ in range(n_steps):
            cur = _step_coupon_forward(cur, payments_per_year)
        return cur
    for _ in range(-n_steps):
        cur = _step_coupon_backward(cur, payments_per_year)
    return cur


def _prix_ct_semaines(
    d_liq: date,
    d_em: date,
    d_mat: date,
    taux: float,
    nominal: float,
    payments_per_year: int,
) -> tuple[list[date], list[float]]:
    """CT 13/26/52 : un flux à l’échéance, ``Round(..., 2)`` comme VBA."""
    days = (d_mat - d_em).days
    if days <= 0:
        return [], []
    fd: list[date] = [d_mat]
    if (d_mat - d_em).days / 365.0 > 1.0:
        prev = _step_coupon_backward(d_mat, payments_per_year)
        den = max(1, (d_mat - prev).days)
        flux = round(nominal * (1.0 + taux * float((d_mat - d_em).days) / float(den)), 2)
    else:
        flux = round(nominal * (1.0 + taux * float((d_mat - d_em).days) / 360.0), 2)
    return fd, [flux]


def _nbr_flux_mlt_vba(d_liq: date, d_jou: date, d_mat: date, payments_per_year: int) -> int:
    """Compte les flux VBA : ``Do While date_flux > liquidation And date_flux <> jouissance``."""
    nbr = 0
    d_flux = d_mat
    guard = 0
    while d_flux > d_liq and d_flux != d_jou and guard < 2000:
        nbr += 1
        nxt = _step_coupon_backward(d_flux, payments_per_year)
        if nxt >= d_flux:
            break
        d_flux = nxt
        guard += 1
    return nbr


def _echeancier_ligne_vba(d_mat: date, i_un: int, nbr_flux: int, payments_per_year: int) -> date:
    """``echeancier(i) = ajouter(échéance, i - nbr_flux)`` (indices VBA 1-based)."""
    return _ajouter_periodes_coupon(d_mat, i_un - nbr_flux, payments_per_year)


def _prix_mlt_in_fine_flows_vba(
    d_liq: date,
    d_em: date,
    d_jou: date,
    d_mat: date,
    nominal: float,
    taux_annuel: float,
    payments_per_year: int,
    *,
    taux_coupon_comme_vba: bool = False,
) -> tuple[list[date], list[float]]:
    """
    Flux MLT remboursement in fine (``periodicite_cap`` = F), même structure que la boucle VBA
    (échéancier + montants arrondis à 2 déc.).

    - ``taux_coupon_comme_vba=False`` : facial **annuel** → coupon = ``nominal * taux / pay``.
    - ``taux_coupon_comme_vba=True`` : comme VBA ``nominal * taux`` (taux lu comme pour un versement).
    """
    pay = max(1, int(payments_per_year))
    cpn = nominal * taux_annuel if taux_coupon_comme_vba else nominal * taux_annuel / float(pay)
    nbr = _nbr_flux_mlt_vba(d_liq, d_jou, d_mat, pay)
    if nbr <= 0:
        return [], []
    fd = [_echeancier_ligne_vba(d_mat, i, nbr, pay) for i in range(1, nbr + 1)]
    ec1 = fd[0]
    prev1 = _step_coupon_backward(ec1, pay)
    pd1 = max(1, (ec1 - prev1).days)
    d_jou_plus1 = _step_coupon_forward(d_jou, pay)
    fm: list[float] = [0.0] * nbr

    if nbr == 1:
        if d_liq < d_jou_plus1:
            if taux_coupon_comme_vba:
                fm[0] = round(
                    nominal + nominal * taux_annuel * float((ec1 - d_em).days) / float(pd1),
                    2,
                )
            else:
                fm[0] = round(nominal + cpn * float((ec1 - d_em).days) / float(pd1), 2)
        else:
            if taux_coupon_comme_vba:
                fm[0] = round(nominal * (1.0 + taux_annuel), 2)
            else:
                fm[0] = round(nominal + cpn, 2)
        return fd, fm

    if d_liq < d_jou_plus1:
        if taux_coupon_comme_vba:
            fm[0] = round(nominal * taux_annuel * float((ec1 - d_em).days) / float(pd1), 2)
        else:
            fm[0] = round(cpn * float((ec1 - d_em).days) / float(pd1), 2)
        for j in range(1, nbr - 1):
            fm[j] = round(cpn, 2)
        if taux_coupon_comme_vba:
            fm[nbr - 1] = round(nominal * (1.0 + taux_annuel), 2)
        else:
            fm[nbr - 1] = round(nominal + cpn, 2)
    else:
        for j in range(0, nbr - 1):
            fm[j] = round(cpn, 2)
        if taux_coupon_comme_vba:
            fm[nbr - 1] = round(nominal * (1.0 + taux_annuel), 2)
        else:
            fm[nbr - 1] = round(nominal + cpn, 2)
    return fd, fm


def _actuariel_vba_stub_period(
    d_liq: date,
    d_first: date,
    payments_per_year: int,
    premier_j_inclus: bool,
) -> tuple[float, int]:
    """``stub`` et longueur de période (jours) comme en VBA (fraction jusqu’au 1er flux)."""
    d_prev = _step_coupon_backward(d_first, payments_per_year)
    period_len = max(1, (d_first - d_prev).days)
    ji = 1 if premier_j_inclus else 0
    stub = ((d_first - d_liq).days - ji) / float(period_len)
    return max(0.0, stub), period_len


def _flux_arrondis_atp(fm: list[float], decimals: int = 2) -> list[float]:
    return [round(float(x) + 1e-12, decimals) for x in fm]


def pv_actuariel_mode_a(
    d_liq: date,
    fd: list[date],
    fm: list[float],
    y_annuel: float,
    premier_j_inclus: bool,
    payments_per_year: int,
    *,
    actuariel_base: int = 1,
) -> float:
    """
    Actualisation mode A : ``p += flux(i) / (1+rendement)^di``.

    - ``actuariel_base == 1`` (défaut, VBA ``base = 1``) : ``di = (i-1) + stub`` avec stub en
      fraction de période coupon (échéancier(1) − liquidation − jour_inclus) / longueur période.
    - ``actuariel_base == 2`` (VBA ``base = 2`` si activé dans le classeur) :
      ``di = (date_flux(i) − liquidation − jour_inclus) / 365`` (fraction d’année ACT/365).
    """
    if not fd or y_annuel <= -1:
        return 0.0
    fm_use = _flux_arrondis_atp(fm)
    pv = 0.0
    ji = 1 if premier_j_inclus else 0
    if int(actuariel_base) == 2:
        for j, fl in enumerate(fm_use):
            di = max(0.0, float((fd[j] - d_liq).days) - float(ji)) / 365.0
            pv += fl / math.pow(1.0 + y_annuel, di)
        return pv
    d_first = fd[0]
    stub, _period_len = _actuariel_vba_stub_period(d_liq, d_first, payments_per_year, premier_j_inclus)
    for j, fl in enumerate(fm_use):
        di = float(j) + stub
        pv += fl / math.pow(1.0 + y_annuel, di)
    return pv


def ytm_actuariel_mode_a(
    d_liq: date,
    fd: list[date],
    fm: list[float],
    prix_clean_cible: float,
    premier_j_inclus: bool,
    payments_per_year: int,
    *,
    actuariel_base: int = 1,
) -> float:
    """Taux actuariel annuel (décimal) tel que ``pv_actuariel_mode_a`` = prix clean cible."""

    def px(y: float) -> float:
        return pv_actuariel_mode_a(
            d_liq,
            fd,
            fm,
            y,
            premier_j_inclus,
            payments_per_year,
            actuariel_base=actuariel_base,
        )

    if prix_clean_cible <= 0 or not fd or not fm:
        return float("nan")
    lo, hi = -0.99, 0.5
    for _ in range(200):
        ph = px(hi)
        if not math.isfinite(ph):
            break
        if ph < prix_clean_cible:
            hi = min(hi + 0.5, 50.0)
        else:
            break
    fa, fb = px(lo) - prix_clean_cible, px(hi) - prix_clean_cible
    if not math.isfinite(fa) or not math.isfinite(fb) or fa * fb > 0:
        # Repli : grille sur y (obligations exigeantes, primes, etc.)
        prev_y, prev_e = None, None
        for k in range(-10, 1001):
            y = -0.5 + 0.005 * k
            e = px(y) - prix_clean_cible
            if not math.isfinite(e):
                continue
            if prev_e is not None and prev_e * e <= 0:
                lo, hi = prev_y, y
                fa, fb = prev_e, e
                break
            prev_y, prev_e = y, e
        else:
            return float("nan")
        if fa * fb > 0:
            return float("nan")
    a, b = (lo, hi) if fa < 0 else (hi, lo)
    fa, fb = px(a) - prix_clean_cible, px(b) - prix_clean_cible
    for _ in range(200):
        m = 0.5 * (a + b)
        fmid = px(m) - prix_clean_cible
        if abs(fmid) < 1e-11:
            return m
        if fa * fmid <= 0:
            b, fb = m, fmid
        else:
            a, fa = m, fmid
    return float(0.5 * (a + b))


def prix_atp_dbt(
    *,
    date_liquidation: date,
    date_emission: date,
    date_jouissance: date,
    date_echeance: date,
    taux_coupon_annuel: float,
    nominal: float,
    premier_j_inclus: bool,
    mode_valorisation: str,
    periodicite_cp: int,
    periodicite_cap_fin: bool,
    rendement_annuel_effectif: float,
    maturite_semaines_ct: int | None = None,
    actuariel_base: int = 1,
    taux_coupon_comme_vba: bool = False,
) -> dict[str, Any]:
    """
    Reproduit la structure de ``prix_ATP`` : retourne prix clean, coupon couru, prix dirty, flux.

    ``taux_coupon_annuel`` et ``rendement_annuel_effectif`` sont en **décimal** (ex. 0,11 pour 11 %).

    ``actuariel_base`` : dernier argument VBA ``base`` (1 = exposants en périodes coupon, 2 = jours/365 par flux).

    ``taux_coupon_comme_vba`` : si True, mêmes montants de coupon que le VBA ``nominal * taux`` (taux « par versement »
    ou facial annuel avec coupon annuel uniquement — à activer via colonne ``ATP_COUPON_VBA`` si besoin).
    """
    try:
        _bv = int(round(float(actuariel_base)))
        ab = _bv if _bv in (1, 2) else 1
    except (TypeError, ValueError):
        ab = 1
    tca = float(taux_coupon_annuel)
    # Toujours pour les taux en **décimal annuel** (|t| ≤ 1), y compris si ``ATP_COUPON_VBA`` est activé :
    # une colonne Excel mal renseignée laissait 5,599 % sans arrondi 5,60 % (coupon couru 5276,8658).
    if math.isfinite(tca) and abs(tca) <= 1.0:
        tca = _normalise_taux_coupon_annuel_wg_deux_dec_pct(tca)
    mode_key = normaliser_mode_valo(mode_valorisation) or "A"
    mchar = mode_key[:1] if mode_key else "A"

    if date_echeance <= date_liquidation:
        return {
            "prix_clean": 0.0,
            "coupon_courru": 0.0,
            "prix_dirty": 0.0,
            "flux_dates": [],
            "flux_montants": [],
            "mode_utilise": mchar,
        }

    pay_per_year = max(1, int(periodicite_cp))

    # ----- CT semaines -----
    if maturite_semaines_ct in (13, 26, 52):
        fd, fm = _prix_ct_semaines(
            date_liquidation,
            date_emission,
            date_echeance,
            tca,
            nominal,
            pay_per_year,
        )
        if not fd:
            return {
                "prix_clean": 0.0,
                "coupon_courru": 0.0,
                "prix_dirty": 0.0,
                "flux_dates": [],
                "flux_montants": [],
                "mode_utilise": mchar,
            }
        d_next = fd[0]
        d_last = _step_coupon_backward(d_next, pay_per_year)
        cc = coupon_courru_atp(
            date_liquidation,
            d_last,
            d_next,
            nominal,
            tca,
            pay_per_year,
            premier_j_inclus=premier_j_inclus,
            d_jouissance=date_jouissance,
            taux_coupon_comme_vba=taux_coupon_comme_vba,
        )
        if mchar == "M":
            num = (fd[0] - date_liquidation).days
            pclean = _pv_clean_mode_m_act_360(fm[0], rendement_annuel_effectif, num)
        elif mchar == "L":
            pclean = float(nominal)
        else:
            pclean = pv_actuariel_mode_a(
                date_liquidation,
                fd,
                fm,
                rendement_annuel_effectif,
                premier_j_inclus,
                pay_per_year,
                actuariel_base=ab,
            )
        if mchar == "L":
            pdirty = float(nominal + cc)
        else:
            pdirty = pclean + cc
        return {
            "prix_clean": float(pclean),
            "coupon_courru": float(cc),
            "prix_dirty": float(pdirty),
            "flux_dates": fd,
            "flux_montants": fm,
            "mode_utilise": mchar,
        }

    # ----- MLT -----
    if not periodicite_cap_fin:
        # Amortissements : non implémenté ici — laisser le code appelant retomber sur la ZC.
        return {
            "prix_clean": float("nan"),
            "coupon_courru": 0.0,
            "prix_dirty": float("nan"),
            "flux_dates": [],
            "flux_montants": [],
            "mode_utilise": mchar,
            "amortissement_non_supporte": True,
        }

    fd, fm = _prix_mlt_in_fine_flows_vba(
        date_liquidation,
        date_emission,
        date_jouissance,
        date_echeance,
        nominal,
        tca,
        pay_per_year,
        taux_coupon_comme_vba=taux_coupon_comme_vba,
    )
    if not fd:
        return {
            "prix_clean": 0.0,
            "coupon_courru": 0.0,
            "prix_dirty": 0.0,
            "flux_dates": [],
            "flux_montants": [],
            "mode_utilise": mchar,
        }

    d_next = fd[0]
    d_last = _step_coupon_backward(d_next, pay_per_year)
    cc = coupon_courru_atp(
        date_liquidation,
        d_last,
        d_next,
        nominal,
        tca,
        pay_per_year,
        premier_j_inclus=premier_j_inclus,
        d_jouissance=date_jouissance,
        taux_coupon_comme_vba=taux_coupon_comme_vba,
    )

    if mchar == "M":
        num = (fd[0] - date_liquidation).days
        pclean = _pv_clean_mode_m_act_360(fm[0], rendement_annuel_effectif, num)
    elif mchar == "L":
        pclean = float(nominal)
    else:
        pclean = pv_actuariel_mode_a(
            date_liquidation,
            fd,
            fm,
            rendement_annuel_effectif,
            premier_j_inclus,
            pay_per_year,
            actuariel_base=ab,
        )

    if mchar == "L":
        pdirty = float(nominal + cc)
    else:
        pdirty = pclean + cc
    return {
        "prix_clean": float(pclean),
        "coupon_courru": float(cc),
        "prix_dirty": float(pdirty),
        "flux_dates": fd,
        "flux_montants": fm,
        "mode_utilise": mchar,
    }


def _ytm_simple(cfs: np.ndarray, times: np.ndarray, price_target: float) -> float:
    def px(y: float) -> float:
        return float(np.sum(cfs / np.power(1.0 + y, times)))

    if price_target <= 0 or cfs.size == 0:
        return float("nan")
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


def metriques_depuis_flux_atp(
    d_liq: date,
    flux_dates: list[date],
    flux_montants: list[float],
    prix_clean_cible: float,
    rendement_injecte: float,
    *,
    premier_j_inclus: bool = False,
    periodicite_cp: int = 1,
    mode_atp: str = "A",
    actuariel_base: int = 1,
    metric_day_base: float = 365.0,
    convexity_day_base: float | None = None,
    convexity_actuarial_first_flow: bool = False,
) -> dict[str, float]:
    """Durations, convexité ; en mode A le **YTM renvoyé** = ``rendement_injecte`` (taux de valorisation)."""
    if not flux_dates or not flux_montants or not math.isfinite(prix_clean_cible):
        return {
            "ytm": float("nan"),
            "duration_macaulay": 0.0,
            "duration_modifiee": 0.0,
            "convexite": 0.0,
        }
    mchar = (mode_atp or "A").strip().upper()[:1]
    cx_day_base = float(convexity_day_base) if convexity_day_base is not None else float(metric_day_base)

    if mchar == "A":
        # Prix clean = PV_actuariel (stub si base 1, jours/365 si base 2) — inchangé côté prix_ATP.
        # Duration / convexité : alignés VBA ``duration_titre`` / ``convexite_titre`` (mode A) :
        #   di = (date_flux(i) - liquidation - jour_inclus) / 365
        #   PV_i = flux_i / (1+rendement)^di  ;  duration = sum(di * PV) / sum(PV)
        #   convexité = (sum(PV * di * (di+1)) / sum(PV)) / (1+rendement)^2
        # (indépendant de l’exposant « stub + période » utilisé pour le prix si base = 1.)
        ytm = float(rendement_injecte)
        cfs = np.array(_flux_arrondis_atp(flux_montants), dtype=float)
        ji = 1 if premier_j_inclus else 0
        if ytm <= -1 or cfs.size == 0:
            d_mac = d_mod = cx = 0.0
        else:
            di = np.array(
                [
                    max(0.0, float((flux_dates[j] - d_liq).days) - float(ji)) / float(metric_day_base)
                    for j in range(len(cfs))
                ],
                dtype=float,
            )
            dfs = np.power(1.0 + ytm, -di)
            pvs = cfs * dfs
            p = float(pvs.sum())
            if p <= 0:
                d_mac = d_mod = cx = 0.0
            else:
                d_mac = float(np.sum(di * pvs) / p)
                d_mod = d_mac / (1.0 + ytm) if abs(1.0 + ytm) > 1e-15 and math.isfinite(ytm) else 0.0
                ci = np.array(
                    [
                        max(0.0, float((flux_dates[j] - d_liq).days) - float(ji)) / float(cx_day_base)
                        for j in range(len(cfs))
                    ],
                    dtype=float,
                )
                pvs_cx = cfs * np.power(1.0 + ytm, -ci)
                p_cx = float(pvs_cx.sum())
                if p_cx <= 0:
                    cx = 0.0
                else:
                    c_sum = float(np.sum(pvs_cx * ci * (ci + 1.0)))
                    cx = (c_sum / p_cx) / ((1.0 + ytm) ** 2)
        return {
            "ytm": float(ytm),
            "duration_macaulay": round(d_mac, 6),
            "duration_modifiee": round(d_mod, 6),
            "convexite": round(cx, 6),
        }

    # M / L : le prix clean ne suit pas (1+y)^t en années exactes ; un « YTM » résolu
    # avec _ytm_simple serait faux vs Excel. On expose le taux de valorisation injecté.
    cfs = np.asarray(flux_montants, dtype=float)
    t = np.array([(d - d_liq).days / float(metric_day_base) for d in flux_dates], dtype=float)
    ytm = float(rendement_injecte)
    if mchar == "M" and len(flux_dates) == 1:
        jours = max(0, (flux_dates[0] - d_liq).days)
        tau = float(jours) / 360.0
        den = 1.0 + ytm * tau
        if den > 0.0 and math.isfinite(den):
            d_mod = tau / den
            d_mac = (1.0 + ytm) * d_mod
            if convexity_actuarial_first_flow:
                cx = (tau * (tau + 1.0)) / ((1.0 + ytm) ** 2)
            else:
                cx = 2.0 * tau * tau / (den * den)
            return {
                "ytm": float(ytm),
                "duration_macaulay": round(d_mac, 6),
                "duration_modifiee": round(d_mod, 6),
                "convexite": round(cx, 6),
            }
    dfs = np.power(1.0 + rendement_injecte, -t)
    pvs = cfs * dfs
    p = float(pvs.sum())
    if p <= 0:
        d_mac = 0.0
    else:
        d_mac = float(np.sum(t * pvs) / p)
    d_mod = d_mac / (1.0 + ytm) if abs(1.0 + ytm) > 1e-15 and math.isfinite(ytm) else 0.0
    terms = t * (t + 1.0) * cfs / np.power(1.0 + ytm, t + 2.0)
    cx = float(terms.sum() / prix_clean_cible) if prix_clean_cible > 0 else 0.0
    return {
        "ytm": float(ytm),
        "duration_macaulay": round(d_mac, 6),
        "duration_modifiee": round(d_mod, 6),
        "convexite": round(cx, 6),
    }


def normaliser_mode_valo(v: Any) -> str:
    """Normalise le libellé Excel/VBA : « Marché » = actuariel (A), pas monétaire (M)."""
    raw = str(v or "").strip()
    if not raw:
        return ""
    s = raw.upper().replace("É", "E").replace("È", "E").replace("Ê", "E")
    if s.startswith("AA"):
        return "A"
    if "MARCHE" in s:
        return "A"
    if s.startswith("A"):
        return "A"
    if s.startswith("M"):
        return "M"
    if s.startswith("L"):
        return "L"
    return s[:1] if s else ""
