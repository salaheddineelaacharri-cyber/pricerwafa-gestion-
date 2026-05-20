"""Trace cellule par cellule de l'échéancier de 9500 pour comparer aux valeurs Excel.

Affiche pour chaque colonne future (Excel D…AG) :
- date paiement, jours, durée (chain rule), Flux restant, Taux ZC, Prime, Taux d'actu,
  Flux actualisé. La somme des Flux actualisés donne le Prix.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import main as api  # noqa: E402


def main() -> None:
    pillars = api._extraire_piliers_depuis_histo(ROOT, "2026-03-26", "MAR_JJ")
    curve = api.CurveRequest(
        short=pillars["short"],
        long=pillars["long"],
        joint_days=325,
        max_days=11000,
        step_short=50,
        step_long=100,
    )
    req = api.MarcheValorizeRequest(
        valuation_date="2026-03-26",
        curve=curve,
        feuil1_pricer_tous=True,
    )
    res = api.marche_valorize(req)
    if hasattr(res, "body"):
        res = json.loads(res.body)

    tables = res.get("amortissement_tables", [])
    for t in tables:
        code = str(t.get("CODE") or t.get("code") or "").strip()
        if code != "9500":
            continue
        rows = t.get("rows") or []
        cols = t.get("columns") or []
        # Print interesting columns: date, jours, durée, flux restant, taux zc, prime, taux actu, flux actualisé
        labels_we_want = (
            "Date d'échéance",
            "Date de tombée",
            "jours",
            "Durée",
            "duree",
            "Flux restant",
            "Taux ZC",
            "Prime",
            "Taux d'actualisation",
            "Flux actualisé",
        )
        print("Columns of table:", cols[:10], "..." if len(cols) > 10 else "")
        for r in rows:
            if not isinstance(r, dict):
                continue
            for k, v in r.items():
                if any(lbl.lower() == str(k).lower() for lbl in labels_we_want):
                    print(f"  {k}: {v}")
            print("  ---")
        if rows and isinstance(rows[0], dict) and "values" in rows[0]:
            for r in rows:
                lbl = r.get("label")
                vals = r.get("values")
                print(f"  [{lbl}] {vals}")
        print()
        return
    print("9500 introuvable dans amortissement_tables")
    print("Tables présentes:", [str(t.get("CODE") or t.get("code")) for t in tables])


if __name__ == "__main__":
    main()
