"""Tests pricing obligations révisables (REV) — formule Excel AWB."""

from __future__ import annotations

import math
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.app.services.bond_pricing import (  # noqa: E402
    calculate_rev_bond_price,
    prix_rev_actualise_excel_puissance,
    prix_rev_lineaire_act360,
    taux_actualisation_rev_arrondi_excel,
)
from datetime import date

from obligation_amort_schedule import (  # noqa: E402
    _cellule_texte_excel_normalisee,
    _date_coupon_precedent_rr,
    _date_valorisation_oblig_depuis_ref,
    _spread_depuis_ref,
    _pct_taux_courbe_fix_aa_display,
    _est_colonne_type_taux,
    _type_taux_est_rev,
    _valeur_type_taux_indique_rev,
    appliquer_grille_amort_sur_lignes_marche,
    construire_tableau_amortissement,
)
import pandas as pd


def test_date_valo_referentiel_override_c1():
    g = date(2025, 6, 4)
    row = pd.Series({"CODE": "9685", "DATE_VALO": "2026-03-26"})
    assert _date_valorisation_oblig_depuis_ref(row, g) == date(2026, 3, 26)
    assert _date_valorisation_oblig_depuis_ref(None, g) == g


def test_type_taux_rev_excel_espaces_apostrophe():
    assert _cellule_texte_excel_normalisee(" REV") == "REV"
    assert _cellule_texte_excel_normalisee("\xa0REV ") == "REV"
    assert _cellule_texte_excel_normalisee("'REV") == "REV"
    assert _valeur_type_taux_indique_rev(" REV")
    assert _est_colonne_type_taux("TYPE_TAUX")
    assert _est_colonne_type_taux("TYPE TAUX")
    row = pd.Series({"CODE": "9685", "TYPE_TAUX": " REV"})
    assert _type_taux_est_rev(row, "") is True


def test_type_taux_rev_ne_prend_pas_le_libelle_seul():
    row = pd.Series({"CODE": "9685", "DESCRIPTION": "OAT REV 26 SEM 4 ANS"})
    assert _type_taux_est_rev(row, "OAT REV 26 SEM 4 ANS") is False


def test_rev_taux_aa_plus_prime_arrondi_excel():
    pct, dec = taux_actualisation_rev_arrondi_excel(0.02283, 0.0055, ndigits_pct=5)
    assert math.isclose(pct, 2.833, rel_tol=0, abs_tol=1e-9)
    assert math.isclose(dec, 0.02833, rel_tol=0, abs_tol=1e-12)


def test_rev_pricing_numeric_excel_9685_act360_70j():
    """
    Cas de contrôle issu du classeur (Code 9685) : ``t = 70/360`` (ACT/360), pas /365.

    Numérateur : 13 747,46 + 75 000 = 88 747,46
    Dénominateur : 1 + 0,02833 × (70/360)
    Prix attendu : 88 261,26
    """
    _, r = taux_actualisation_rev_arrondi_excel(0.02283, 0.0055)
    prix = prix_rev_lineaire_act360(13747.46, 75000.0, r, 70)
    assert math.isclose(prix, 88261.26, rel_tol=0, abs_tol=0.02)


def test_rev_duree_excel_c1_valo_26_03_2026_revision_04_06_2026():
    """
    Source Excel : ``$C$1`` = date de valorisation, durée ``=(Date_colonne - $C$1)/360``.

    (04/06/2026 − 26/03/2026) = 70 jours → 70/360 = 0,19444444…
    Même chaîne que ``prix_rev_lineaire_act360`` pour le Code 9685.
    """
    from datetime import date

    date_valorisation = date(2026, 3, 26)
    date_revision = date(2026, 6, 4)
    jours = (date_revision - date_valorisation).days
    assert jours == 70
    duree = jours / 360.0
    assert math.isclose(duree, 0.19444444, rel_tol=0, abs_tol=1e-8)

    _, r = taux_actualisation_rev_arrondi_excel(0.02283, 0.0055)
    prix = prix_rev_lineaire_act360(13747.46, 75000.0, r, jours)
    assert math.isclose(prix, 88261.26, rel_tol=0, abs_tol=0.02)


def test_calculate_rev_bond_price_dynamic_excel_case_1():
    df = pd.DataFrame(
        [
            {"CODE": "9685", "DATE_REGLEMENT": "2025-12-04"},
            {"CODE": "9685", "DATE_REGLEMENT": "2026-06-04"},
            {"CODE": "9685", "DATE_REGLEMENT": "2026-12-04"},
        ]
    )
    _, r = taux_actualisation_rev_arrondi_excel(0.02283, 0.0055)
    prix, jours, duree, d_rev = calculate_rev_bond_price(
        date_valorisation=date(2026, 3, 26),
        df_echeancier=df,
        flux_prochain=13747.46,
        capital_restant=75000.0,
        taux_actualisation_decimal=r,
        code="9685",
    )
    assert d_rev == date(2026, 6, 4)
    assert jours == 70
    assert math.isclose(duree, 70.0 / 360.0, rel_tol=0, abs_tol=1e-12)
    assert math.isclose(prix, 88261.26, rel_tol=0, abs_tol=0.02)


def test_rev_zc_puissance_excel_5156_flexenergy():
    """
    Classeur FLEXENERGY / 5156 : ``=(T8+V8)/(1+Z8)^Y8`` avec Z = taux actu. décimal, Y = durée ligne.
    Valeur de référence Excel (flux actualisé) : 100 728,9393.
    """
    pv = prix_rev_actualise_excel_puissance(
        100000.0 + 3740.0,
        0.03872,
        0.7753424658,
    )
    assert math.isclose(pv, 100728.9393, rel_tol=0, abs_tol=0.02)


def test_calculate_rev_bond_price_dynamic_excel_case_2_changes_with_valo_date():
    df = pd.DataFrame([{"CODE": "9685", "DATE_REGLEMENT": "2026-06-04"}])
    _, r = taux_actualisation_rev_arrondi_excel(0.02283, 0.0055)
    prix, jours, duree, _ = calculate_rev_bond_price(
        date_valorisation=date(2025, 6, 4),
        df_echeancier=df,
        flux_prochain=13747.46,
        capital_restant=75000.0,
        taux_actualisation_decimal=r,
        code="9685",
    )
    assert jours == 365
    assert math.isclose(duree, 365.0 / 360.0, rel_tol=0, abs_tol=1e-12)
    assert not math.isclose(prix, 88261.26, rel_tol=0, abs_tol=0.01)


def test_spread_emission_depuis_referentiel_en_bp():
    row = pd.Series({"SPREAD_EMISSION": 70})
    assert math.isclose(_spread_depuis_ref(row, 0.0), 0.007, rel_tol=0, abs_tol=1e-12)


def test_rr_date_coupon_precedent_annuel():
    assert _date_coupon_precedent_rr(date(2026, 12, 29), "AN") == date(2025, 12, 29)


def test_fix_aa_taux_aa_display_arrondi_5dec_fichier_643j():
    """Interpolation 365/730 (courbe fichier) à 643j : ARRONDI 5 dec. (demi pair) → 2,633 % (0,02633)."""
    r = (0.02669800 - 0.02514317) / (730.0 - 365.0) * (643.0 - 365.0) + 0.02514317
    assert math.isclose(r, 0.026327396684931507, rel_tol=0, abs_tol=1e-12)
    assert math.isclose(_pct_taux_courbe_fix_aa_display(r), 2.633, rel_tol=0, abs_tol=1e-9)


def test_fix_aa_taux_aa_display_arrondi_5dec_piliers_bam_lt_643j():
    """Maturité 643 j sur grille LT encadrée (326–1481) : interpolé ~0,026315778 → 2,632 % (pas 2,631 en troncature)."""
    ct = {1: 0.0227, 53: 0.0227, 144: 0.0234, 326: 0.0246, 543: 0.0257058253}
    mlt = {326: 0.02497469, 543: 0.0259, 1481: 0.0298}
    from pricing.curves.zc_interpolation_excel import taux_secondaire_interpole_formule_b

    r = float(taux_secondaire_interpole_formule_b(643.0, ct, mlt, ndigits=None))
    assert math.isclose(r, 0.026315778251599146, rel_tol=0, abs_tol=1e-12)
    assert math.isclose(_pct_taux_courbe_fix_aa_display(r), 2.632, rel_tol=0, abs_tol=1e-9)


def test_fix_aa_taux_aa_display_pre_round12_evite_2631_au_lieu_de_2632():
    """Flottant IEEE légèrement sous 0,026320 : le pré-arrondi 12 dec. stabilise avant quantize 5 dec."""
    r = 0.02632 - 1e-16
    assert math.isclose(_pct_taux_courbe_fix_aa_display(r), 2.632, rel_tol=0, abs_tol=1e-9)


def test_formule_b_transition_convertit_lt_en_monetaire_avant_interpolation_201868():
    """201868 @ 2026-03-06 : zone CT/LT 255j -> 2,334 % affiché."""
    from pricing.curves.zc_interpolation_excel import taux_secondaire_interpole_formule_b

    ct = {
        1.0: 0.0225,
        73.0: 0.0225,
        164.0: 0.0229,
        192.0: 0.0230,
    }
    lt = {
        192.0: 0.023448264792397255,
        374.0: 0.024300000000000002,
        1501.0: 0.0274,
    }
    r = float(taux_secondaire_interpole_formule_b(255.0, ct, lt, ndigits=None))

    assert math.isclose(r, 0.0233372395261384, rel_tol=0, abs_tol=1e-14)
    assert math.isclose(round(r * 100.0, 3), 2.334, rel_tol=0, abs_tol=1e-12)
    assert math.isclose(_pct_taux_courbe_fix_aa_display(r), 2.334, rel_tol=0, abs_tol=1e-12)


def test_fix_aa_rr_utilise_spread_referentiel_et_duree_periode_reelle():
    ref_row = pd.Series(
        {
            "CODE": "9489",
            "TYPE_TAUX": "FIX",
            "METHODE_VALO": "AA",
            "SPREAD_EMISSION": 70,
            "PERIODICITE_COUPON": "AN",
            "BASE_CALCUL": "R/R",
        }
    )
    lignes = [
        {"date": date(2025, 12, 29), "amortissement": 0.0, "interet_excel": 0.0, "flux_excel": 0.0},
        {"date": date(2026, 12, 29), "amortissement": 0.0, "interet_excel": 2970.0, "flux_excel": 2970.0},
        {"date": date(2027, 12, 29), "amortissement": 100000.0, "interet_excel": 2970.0, "flux_excel": 102970.0},
    ]

    table = construire_tableau_amortissement(
        "9489",
        lignes,
        nominal=100000.0,
        taux_coupon_dec=0.0297,
        description="Test FIX AA",
        note_ref=None,
        d_valo=date(2026, 3, 26),
        spread_dec=0.0,
        taux_secondaire_a_j=lambda jours: 0.0262,
        taux_zc_table_dec=None,
        taux_zc_schedule_j=None,
        rev_bond=False,
        fix_bond=True,
        ref_row=ref_row,
    )
    rows_by_label = {row["label"]: row["values"] for row in table["rows"]}

    assert math.isclose(table["spread_decimal_reference"], 0.007, rel_tol=0, abs_tol=1e-12)
    # FIX + AA : taux d'actualisation unique = taux de la **dernière** tombée (colonne 2027) répliqué sur 2026.
    ta_2026 = rows_by_label["Taux d'actualisation"][1]
    ta_2027 = rows_by_label["Taux d'actualisation"][2]
    assert math.isclose(ta_2026, ta_2027, rel_tol=0, abs_tol=1e-9)
    assert math.isclose(ta_2027, 3.32, rel_tol=0, abs_tol=1e-9)
    assert math.isclose(rows_by_label["durée"][1], round(278.0 / 365.0, 8), rel_tol=0, abs_tol=1e-10)
    fa = rows_by_label["Flux actualisé"]
    r = 0.0332
    d1 = 278.0 / 365.0
    d2 = d1 + 1.0
    pv_att = 2970.0 / (1.0 + r) ** d1 + 102970.0 / (1.0 + r) ** d2
    assert math.isclose(float(fa[1]) + float(fa[2]), pv_att, rel_tol=0, abs_tol=0.02)


def test_fix_aa_unique_taux_derniere_tombe_manar_style_9489():
    """Cas Manar : taux AA interpolé à 643j + spread, répliqué sur toutes les tombées futures."""

    def ts(j: float) -> float:
        jf = float(j)
        # Interpolation linéaire entre les piliers fallback ``courbe_zc.py`` (365/730).
        t0, t1 = 0.02514317, 0.02669800
        return (t1 - t0) / (730.0 - 365.0) * (jf - 365.0) + t0

    ref_row = pd.Series(
        {
            "CODE": "9489",
            "TYPE_TAUX": "FIX",
            "METHODE_VALO": "AA",
            "SPREAD_EMISSION": 70,
            "PERIODICITE_COUPON": "AN",
            "BASE_CALCUL": "R/R",
        }
    )
    lignes = [
        {"date": date(2025, 12, 29), "amortissement": 0.0, "interet_excel": 0.0, "flux_excel": 0.0},
        {"date": date(2026, 12, 29), "amortissement": 0.0, "interet_excel": 2970.0, "flux_excel": 2970.0},
        {"date": date(2027, 12, 29), "amortissement": 100000.0, "interet_excel": 2970.0, "flux_excel": 102970.0},
    ]
    table = construire_tableau_amortissement(
        "9489",
        lignes,
        nominal=100000.0,
        taux_coupon_dec=0.0297,
        description="Test FIX AA",
        note_ref=None,
        d_valo=date(2026, 3, 26),
        spread_dec=0.0,
        taux_secondaire_a_j=ts,
        taux_zc_table_dec=None,
        taux_zc_schedule_j=None,
        rev_bond=False,
        fix_bond=True,
        ref_row=ref_row,
    )
    rows_by_label = {row["label"]: row["values"] for row in table["rows"]}
    ta_2026 = rows_by_label["Taux d'actualisation"][1]
    ta_2027 = rows_by_label["Taux d'actualisation"][2]
    assert math.isclose(ta_2026, ta_2027, rel_tol=0, abs_tol=1e-9)
    # Base AA 643 j (fichier) arrondie 5 dec. → 0,02633 + spread 0,007 → 3,333 % (taux unique dernière tombée).
    assert math.isclose(ta_2027, 3.333, rel_tol=0, abs_tol=1e-3)
    fa = rows_by_label["Flux actualisé"]
    assert math.isclose(float(fa[1]) + float(fa[2]), 100087.88, rel_tol=0, abs_tol=0.02)


def test_fix_aa_moins_un_an_restant_mode_monetaire_act360():
    """FIX + AA : une seule tombée future < 365 j => mode monétaire (intérêt simple ACT/360)."""

    ref_row = pd.Series(
        {
            "CODE": "100902",
            "TYPE_TAUX": "FIX",
            "METHODE_VALO": "AA",
            "SPREAD_EMISSION": 70,
            "PERIODICITE_COUPON": "AN",
            "BASE_CALCUL": "R/R",
        }
    )
    # Une seule échéance future après valorisation (titre en "moins d'un an restant").
    lignes = [
        {"date": date(2025, 9, 26), "amortissement": 0.0, "interet_excel": 0.0, "flux_excel": 0.0},
        {"date": date(2026, 6, 4), "amortissement": 100000.0, "interet_excel": 3700.0, "flux_excel": 103700.0},
    ]

    table = construire_tableau_amortissement(
        "100902",
        lignes,
        nominal=100000.0,
        taux_coupon_dec=0.037,
        description="Test FIX AA < 1 an",
        note_ref=None,
        d_valo=date(2026, 3, 26),
        spread_dec=0.0,
        taux_secondaire_a_j=lambda _j: 0.0262,
        taux_zc_table_dec=None,
        taux_zc_schedule_j=None,
        rev_bond=False,
        fix_bond=True,
        ref_row=ref_row,
    )
    rows_by_label = {row["label"]: row["values"] for row in table["rows"]}
    ta = float(rows_by_label["Taux d'actualisation"][1]) / 100.0
    jours = (date(2026, 6, 4) - date(2026, 3, 26)).days
    pv_att = 103700.0 / (1.0 + ta * (jours / 360.0))
    pv_calc = float(rows_by_label["Flux actualisé"][1])
    assert math.isclose(pv_calc, pv_att, rel_tol=0, abs_tol=0.02)


def test_fix_duree_jours_sur_365_si_periodicite_rembou():
    """PERIODICITE_REMBOU renseignée : ligne durée = jours/365 (pas chaînage +1 type période)."""
    ref_row = pd.Series(
        {
            "CODE": "5116",
            "TYPE_TAUX": "FIX",
            "METHODE_VALO": "AA",
            "PERIODICITE_REMBOU": "AN",
            "PERIODICITE_COUPON": "AN",
            "BASE_CALCUL": "R/R",
        }
    )
    d_valo = date(2026, 3, 26)
    lignes = [
        {"date": date(2025, 12, 29), "amortissement": 0.0, "interet_excel": 0.0, "flux_excel": 0.0},
        {"date": date(2026, 12, 29), "amortissement": 0.0, "interet_excel": 100.0, "flux_excel": 100.0},
        {"date": date(2027, 12, 29), "amortissement": 1000.0, "interet_excel": 100.0, "flux_excel": 1100.0},
    ]
    table = construire_tableau_amortissement(
        "5116",
        lignes,
        nominal=100000.0,
        taux_coupon_dec=0.01,
        description="Test durée calendaire",
        note_ref=None,
        d_valo=d_valo,
        spread_dec=0.0,
        taux_secondaire_a_j=lambda _j: 0.03,
        taux_zc_table_dec=None,
        taux_zc_schedule_j=None,
        rev_bond=False,
        fix_bond=True,
        ref_row=ref_row,
    )
    rows_by_label = {row["label"]: row["values"] for row in table["rows"]}
    du = rows_by_label["durée"]
    j1 = (date(2026, 12, 29) - d_valo).days
    j2 = (date(2027, 12, 29) - d_valo).days
    assert math.isclose(float(du[1]), round(j1 / 365.0, 8), rel_tol=0, abs_tol=1e-8)
    assert math.isclose(float(du[2]), round(j2 / 365.0, 8), rel_tol=0, abs_tol=1e-8)


def test_fix_actualisation_utilise_duree_non_arrondie_pour_exposant():
    """Le PV doit utiliser jours/365 exact, même si la ligne durée affichée est arrondie."""
    ref_row = pd.Series(
        {
            "CODE": "9394",
            "TYPE_TAUX": "FIX",
            "METHODE_VALO": "AA",
            "PERIODICITE_COUPON": "AN",
            "PERIODICITE_REMBOU": "AN",
        }
    )
    d_valo = date(2026, 3, 26)
    d_future = d_valo + pd.Timedelta(days=399)
    lignes = [
        {"date": date(2025, 12, 29), "amortissement": 0.0, "interet_excel": 0.0, "flux_excel": 0.0},
        {"date": d_future, "amortissement": 100000.0, "interet_excel": 3500.0, "flux_excel": 103500.0},
    ]

    table = construire_tableau_amortissement(
        "9394",
        lignes,
        nominal=100000.0,
        taux_coupon_dec=0.035,
        description="Test duree exacte 399/365",
        note_ref=None,
        d_valo=d_valo,
        spread_dec=0.0,
        taux_secondaire_a_j=lambda _j: 0.03125,
        taux_zc_table_dec=None,
        taux_zc_schedule_j=None,
        rev_bond=False,
        fix_bond=True,
        ref_row=ref_row,
    )

    rows_by_label = {row["label"]: row["values"] for row in table["rows"]}
    duree_key = next(k for k in rows_by_label if "dur" in k.lower())
    flux_act_key = next(k for k in rows_by_label if "Flux actualis" in k)
    taux_actu_key = next(k for k in rows_by_label if "Taux d'actualisation" in k)
    duree_affichee = float(rows_by_label[duree_key][1])
    pv_calcule = float(rows_by_label[flux_act_key][1])
    r = float(rows_by_label[taux_actu_key][1]) / 100.0
    duree_exacte = 399.0 / 365.0

    pv_exact = 103500.0 / (1.0 + r) ** duree_exacte
    pv_arrondi = 103500.0 / (1.0 + r) ** duree_affichee

    assert math.isclose(duree_affichee, round(duree_exacte, 8), rel_tol=0, abs_tol=1e-12)
    assert math.isclose(pv_calcule, pv_exact, rel_tol=0, abs_tol=0.0001)
    assert not math.isclose(pv_calcule, pv_arrondi, rel_tol=0, abs_tol=0.000001)


def test_fix_zc_amortissable_9394_aligne_durees_et_prix_controle_manar():
    """9394 : FIX + ZC + amortissable annuel -> WG : première durée ARRONDI(;5) puis chaînage +1 ; prix contrôle positif."""
    from pathlib import Path
    from valuation_zc_obligations import (
        charger_courbe_zc_depuis_fichier,
        filtrer_dataframe_par_code_maroclear,
        resoudre_fichier_base_titre_oblig,
        valoriser_dataframe_base_titre,
        interp_taux_secondaire_jours,
    )
    from backend.main import _charger_base_titre_oblg_cache, _row_to_marche_ui
    from obligation_amort_schedule import construire_tables_amortissement_pour_valorisation

    root = Path(__file__).resolve().parents[1]
    curve = charger_courbe_zc_depuis_fichier(root / "pricing" / "curves" / "courbe_zc.py")
    xlsx = resoudre_fichier_base_titre_oblig(root, None)
    df = _charger_base_titre_oblg_cache(xlsx)
    df_work, col_code = filtrer_dataframe_par_code_maroclear(df, "9394")
    df_out, det = valoriser_dataframe_base_titre(df_work, curve, valuation_date="2026-03-26")
    raw = df_out.to_dict(orient="records")
    rows_ui = [_row_to_marche_ui(r, "2026-03-26") for r in raw]

    def _ts(j: float) -> float:
        return float(interp_taux_secondaire_jours(float(j), curve))

    table = construire_tables_amortissement_pour_valorisation(
        xlsx,
        raw,
        rows_ui,
        valuation_date="2026-03-26",
        taux_secondaire_a_j=_ts,
        taux_zc_schedule_j=None,
        df_work=df_work,
        col_code_fichier=col_code,
        det_cols=det,
    )[0]

    rows_by_label = {row["label"]: row["values"] for row in table["rows"]}
    duree_key = next(k for k in rows_by_label if "dur" in k.lower())
    durees = rows_by_label[duree_key]

    from obligation_amort_schedule import _round_excel

    # Feuille WG « Ammortissable » : 1ère durée futur / ZC amort. = ARRONDI(fraction ; 5),
    # puis colonnes suivantes = durée précédente + 1 (cf. ROUND sur la première colonne seule).
    frac_365 = float(34.0 / 365.0)
    first_du = float(_round_excel(frac_365 + 1e-15, 5))

    first_future = next(i for i, v in enumerate(durees) if v is not None and float(v) > 0)
    # La cellule peut exposer la fraction complète ; la règle WG compare à ARRONDI(;5).
    assert math.isclose(round(float(durees[first_future]), 5), first_du, rel_tol=0, abs_tol=1e-9)
    assert math.isclose(round(float(durees[first_future + 1]), 5), round(first_du + 1.0, 5), rel_tol=0, abs_tol=1e-9)
    assert math.isclose(round(float(durees[first_future + 2]), 5), round(first_du + 2.0, 5), rel_tol=0, abs_tol=1e-9)
    assert float(table["prix_somme_flux_actualises"]) > 0.0


def test_fix_tri_duree_par_pas_trimestriel_025_050():
    """FIX + PERIODICITE_COUPON TRI : la durée future suit 0.25, 0.50, ... (pas jours/365)."""
    ref_row = pd.Series(
        {
            "CODE": "5122",
            "TYPE_TAUX": "FIX",
            "METHODE_VALO": "AA",
            "PERIODICITE_COUPON": "TRI",
            "PERIODICITE_REMBOU": "TRI",
        }
    )
    d_valo = date(2026, 3, 26)
    lignes = [
        {"date": date(2025, 12, 29), "amortissement": 0.0, "interet_excel": 0.0, "flux_excel": 0.0},
        {"date": date(2026, 6, 30), "amortissement": 100.0, "interet_excel": 1.0, "flux_excel": 101.0},
        {"date": date(2026, 9, 30), "amortissement": 100.0, "interet_excel": 1.0, "flux_excel": 101.0},
    ]
    table = construire_tableau_amortissement(
        "5122",
        lignes,
        nominal=1000.0,
        taux_coupon_dec=0.01,
        description="Test TRI",
        note_ref=None,
        d_valo=d_valo,
        spread_dec=0.0,
        taux_secondaire_a_j=lambda _j: 0.03,
        taux_zc_table_dec=None,
        taux_zc_schedule_j=None,
        rev_bond=False,
        fix_bond=True,
        ref_row=ref_row,
    )
    rows_by_label = {row["label"]: row["values"] for row in table["rows"]}
    du = rows_by_label["durée"]
    assert math.isclose(float(du[1]), 0.25, rel_tol=0, abs_tol=1e-9)
    assert math.isclose(float(du[2]), 0.50, rel_tol=0, abs_tol=1e-9)


def test_rev_fpct_crd_pv_utilise_capital_restant_sql_seul():
    ref_row = pd.Series(
        {
            "CODE": "TST_FPCT",
            "CATEGORIE": "FPCT",
            "S_CATEGORIE": "FPCTO",
            "TYPE_TAUX": "REV",
            "METHODE_VALO": "AA",
            "PERIODICITE_COUPON": "TRI",
            "PERIODICITE_REMBOU": "FIN",
            "BASE_CALCUL": "R/360",
        }
    )
    lignes = [
        {"date": date(2025, 12, 26), "amortissement": 0.0, "interet_excel": 0.0, "flux_excel": 0.0},
        {
            "date": date(2026, 6, 24),
            "amortissement": 6653.29,
            "interet_excel": 328.33,
            "flux_excel": None,
            "capital_restant_sql": 47062.34,
        },
    ]

    table = construire_tableau_amortissement(
        "TST_FPCT",
        lignes,
        nominal=100000.0,
        taux_coupon_dec=0.0,
        description="Test FPCT",
        note_ref=None,
        d_valo=date(2026, 3, 26),
        spread_dec=0.0,
        taux_secondaire_a_j=lambda _j: 0.0,
        rev_bond=True,
        fix_bond=False,
        ref_row=ref_row,
    )

    dbg = table["debug_rev"]["crd_pv"]
    assert dbg["regle_crd_utilisee"] == "REV_FPCT_CRD_SEUL"
    assert math.isclose(dbg["CRD_DEBUT_PV"], 47062.34, rel_tol=0, abs_tol=1e-9)
    assert math.isclose(dbg["numerateur_PV"], 47390.67, rel_tol=0, abs_tol=1e-9)
    assert math.isclose(float(table["prix_somme_flux_actualises"]), 47390.67, rel_tol=0, abs_tol=1e-9)


def test_rev_ord_amort_crd_pv_reconstruit_crd_debut_sans_impacter_fix():
    lignes = [
        {"date": date(2025, 6, 4), "amortissement": 10000.0, "interet_excel": 0.0, "flux_excel": 10000.0},
        {
            "date": date(2026, 6, 4),
            "amortissement": 10000.0,
            "interet_excel": 610.36,
            "flux_excel": None,
            "capital_restant_sql": 10000.0,
        },
    ]
    ref_rev = pd.Series(
        {
            "CODE": "TST_REV",
            "CATEGORIE": "ORD",
            "TYPE_TAUX": "REV",
            "METHODE_VALO": "AA",
            "PERIODICITE_COUPON": "AN",
            "PERIODICITE_REMBOU": "AN",
            "BASE_CALCUL": "R/R",
        }
    )
    table_rev = construire_tableau_amortissement(
        "TST_REV",
        lignes,
        nominal=20000.0,
        taux_coupon_dec=0.0,
        description="Test REV",
        note_ref=None,
        d_valo=date(2026, 3, 26),
        spread_dec=0.0,
        taux_secondaire_a_j=lambda _j: 0.0,
        rev_bond=True,
        fix_bond=False,
        ref_row=ref_rev,
    )

    dbg = table_rev["debug_rev"]["crd_pv"]
    assert dbg["regle_crd_utilisee"] == "REV_AMORT_CRD_PLUS_AMORT"
    assert math.isclose(dbg["CRD_DEBUT_PV"], 20000.0, rel_tol=0, abs_tol=1e-9)
    assert math.isclose(dbg["numerateur_PV"], 20610.36, rel_tol=0, abs_tol=1e-9)
    assert math.isclose(float(table_rev["prix_somme_flux_actualises"]), 20610.36, rel_tol=0, abs_tol=1e-9)

    ref_fix = pd.Series(
        {
            "CODE": "TST_FIX",
            "CATEGORIE": "ORD",
            "TYPE_TAUX": "FIX",
            "METHODE_VALO": "AA",
            "PERIODICITE_COUPON": "AN",
            "PERIODICITE_REMBOU": "AN",
            "BASE_CALCUL": "R/R",
        }
    )
    table_fix = construire_tableau_amortissement(
        "TST_FIX",
        lignes,
        nominal=20000.0,
        taux_coupon_dec=0.0,
        description="Test FIX",
        note_ref=None,
        d_valo=date(2026, 3, 26),
        spread_dec=0.0,
        taux_secondaire_a_j=lambda _j: 0.0,
        rev_bond=False,
        fix_bond=True,
        ref_row=ref_fix,
    )
    assert table_fix["debug_rev"] is None
    assert math.isclose(float(table_fix["prix_somme_flux_actualises"]), 10610.36, rel_tol=0, abs_tol=1e-9)


def test_prix_arrondi_affiche_somme_flux_actualises_pour_amortissable():
    rows_ui = [
        {
            "CODE": "5122",
            "Prix arrondi": 0.0,
            "Prix clean": 0.0,
            "Prix dirty": 0.0,
            "Coupon couru": 0.0,
        }
    ]
    amort_tables = [
        {
            "code": "5122",
            "description": "Test amortissable",
            "prix_actualise": 1.0,
            "prix_somme_flux_actualises": 40564.15,
            "coupon_couru_schedule": 0.29,
            "ytm_actuariel": 0.03,
            "duration_macaulay": 0.5,
            "duration_modifiee": 0.48,
            "convexite": 0.02,
            "maturite_residuelle_jours": 90,
            "date_echeance_iso": "31/12/2026",
            "taux_coupon_pct": 3.0,
            "nominal_reference": 100000.0,
            "spread_decimal_reference": 0.007,
            "is_amortissable": True,
        }
    ]
    appliquer_grille_amort_sur_lignes_marche(rows_ui, amort_tables)
    r = rows_ui[0]
    assert r.get("_marche_ligne_amortissable") is True
    assert math.isclose(float(r["Prix clean"]), 40564.15, rel_tol=0, abs_tol=1e-9)
    assert math.isclose(float(r["Prix dirty"]), 40564.44, rel_tol=0, abs_tol=1e-9)
    assert math.isclose(float(r["Prix arrondi"]), 40564.15, rel_tol=0, abs_tol=1e-9)
