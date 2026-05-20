"""
Diagnostic valorisation : titres listés × trois dates de courbe.

Usage (racine projet, SQL joignable comme l'API) :
  python scripts/diagnostic_deep_valo_codes.py

Affiche : maturité résiduelle (j), max pilier CT, Formule B (ndigits=None),
taux ZC actuariel échéancier au premier j futur, référentiel METHODE_VALO/CAT.
Ne modifie aucune donnée.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import main as api
from pricing.curves.zc_interpolation_excel import taux_secondaire_interpole_formule_b
from pricing.data_access import charger_referentiel_titre_codes


CODES = ("101005", "100993", "100948", "100995", "5107", "5117")
DATES = ("2026-03-26", "2026-03-06", "2026-01-02")


def _jours_echeance_moins_valo(row, valo_iso: str) -> float | None:
    from valuation_zc_obligations import _jours_echeance_moins_valorisation

    de = row.get("DATE_ECHEANCE") or row.get("date_echeance")
    return _jours_echeance_moins_valorisation(de, valo_iso)


def main() -> None:
    ref = charger_referentiel_titre_codes(list(CODES))
    if ref is None or ref.empty:
        print("referentiel vide ou SQL indisponible")
        return
    col_code = None
    for c in ref.columns:
        if str(c).strip().upper() == "CODE":
            col_code = c
            break
    if not col_code:
        print("colonne CODE introuvable")
        return

    for iso in DATES:
        pillars = api._extraire_piliers_depuis_histo(ROOT, iso, "MAR_JJ")
        s = [float(p["maturity_days"]) for p in pillars["short"]]
        max_ct = max(s) if s else None
        bam_cc, bam_cl = api._courbes_bam_depuis_requete(
            api.CurveRequest(
                short=[api.PillarShort(**p) for p in pillars["short"]],
                long=[api.PillarLong(**p) for p in pillars["long"]],
                joint_days=float(pillars.get("joint_days", 325)),
                max_days=11000,
                step_short=50,
                step_long=100,
            )
        )
        curve = api._make_curve(
            api.CurveRequest(
                short=[api.PillarShort(**p) for p in pillars["short"]],
                long=[api.PillarLong(**p) for p in pillars["long"]],
                joint_days=float(pillars.get("joint_days", 325)),
                max_days=11000,
                step_short=50,
                step_long=100,
            )
        )
        sched = api._schedule_table_records(curve, root=ROOT, date_courbe=iso)
        fn_j = lambda j, rows=sched: api._interp_taux_zc_actuariel_depuis_schedule_jours(float(j), rows)
        print("\n=== COURBE", iso, "joint_long", pillars.get("joint_long_day"), "n_piliers", len(pillars["points"]), "===")

        for code in CODES:
            sub = ref[ref[col_code].astype(str).str.strip() == code]
            if sub.empty:
                print(code, "absent referentiel")
                continue
            row = sub.iloc[0]
            jd = _jours_echeance_moins_valo(row, iso)
            if jd is None or jd <= 0:
                print(code, "maturite invalide", jd)
                continue
            mat_j = float(jd)
            r_b = float(taux_secondaire_interpole_formule_b(mat_j, bam_cc, bam_cl, ndigits=None))
            r_zc_j = float(fn_j(mat_j))
            meth = str(row.get("METHODE_VALO") or row.get("methode_valo") or "").strip()
            cat = str(row.get("CATEGORIE") or row.get("categorie") or "").strip()
            pc = str(row.get("PERIODICITE_COUPON") or row.get("periodicite_coupon") or "").strip()
            pr = str(row.get("PERIODICITE_REMBOU") or row.get("periodicite_rembou") or "").strip()
            bc = str(row.get("BASE_CALCUL") or row.get("base_calcul") or "").strip()
            print(
                json.dumps(
                    {
                        "code": code,
                        "date_courbe": iso,
                        "mat_resid_j": round(mat_j, 4),
                        "max_ct_pilier": max_ct,
                        "formule_b_dec_ndigits_none": round(r_b, 8),
                        "zc_act_schedule_j_eq_mat": round(r_zc_j, 8),
                        "METHODE_VALO": meth,
                        "CATEGORIE": cat,
                        "PERIODICITE_COUPON": pc,
                        "PERIODICITE_REMBOU": pr,
                        "BASE_CALCUL": bc,
                    },
                    ensure_ascii=False,
                )
            )


if __name__ == "__main__":
    main()
