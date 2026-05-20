"""Repro table amort 9424 + compare au classeur (C215 = SOMME des PV arrondis)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import main as m
from obligation_amort_schedule import (
    _parse_date_valo,
    _tenter_table_amort_pour_code,
    charger_referentiel_et_echeancier,
)
from valuation_zc_obligations import taux_secondaire_interpole_formule_b


def main() -> None:
    xlsx = m.resoudre_fichier_base_titre_oblig(ROOT, None)
    ref, ech = charger_referentiel_et_echeancier(xlsx, ["9424"])
    if ref is None or ech is None or ref.empty:
        print("ref/ech manquant")
        return

    pillars = m._extraire_piliers_depuis_histo(ROOT, "2026-03-26", "MAR_JJ")
    curve_req = m.CurveRequest(
        short=[m.PillarShort(**p) for p in pillars["short"]],
        long=[m.PillarLong(**p) for p in pillars["long"]],
        joint_days=float(pillars.get("joint_days", 325.0)),
        max_days=11000,
        step_short=50,
        step_long=100,
    )
    bam_cc, bam_cl = m._courbes_bam_depuis_requete(curve_req)
    curve_tracee = m._make_curve(curve_req)
    sched = m._schedule_table_records(curve_tracee, root=ROOT, date_courbe="2026-03-26")
    fn_zc_j = lambda j, rows=sched: m._interp_taux_zc_actuariel_depuis_schedule_jours(j, rows)
    fn_zc_a = lambda a, rows=sched: m._interp_taux_zc_depuis_schedule_annuel(a, rows)

    def ts_amort(j: float) -> float:
        return float(
            taux_secondaire_interpole_formule_b(float(j), bam_cc, bam_cl, ndigits=None)
        )

    tab = _tenter_table_amort_pour_code(
        code="9424",
        code_s="9424",
        raw={"spread_decimal_valo": 0.0},
        ui={"CODE": "9424", "description": "", "Description": ""},
        ref=ref,
        ech=ech,
        d_valo=_parse_date_valo("2026-03-26"),
        taux_secondaire_a_j=ts_amort,
        taux_zc_schedule_j=fn_zc_j,
        taux_zc_schedule_a=fn_zc_a,
    )
    if tab is None:
        print("table None")
        return

    prix = tab.get("prix_somme_flux_actualises")
    print("prix_somme_flux_actualises:", prix, "(classeur C215 = 75571.4673)")


if __name__ == "__main__":
    main()
