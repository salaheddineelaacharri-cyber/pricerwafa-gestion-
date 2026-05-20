"""Sanity check des 5 codes obligataires de référence (101006, 9651, 9424, 9500, 9351).

Compare le prix moteur (API marche/valorize) au prix lu dans
``2026-PRICER_WG_CORRIGE.xlsm`` après la correction sur l'échéancier ZC.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import main as api  # noqa: E402  (post sys.path)


CODES_REF = {
    "101006": 100577.6813,
    "9651": 101096.3306,
    "9424": 75571.4673,
    "9500": 98542.8764,
    "9351": 47586.4322,
}


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
        prix_manarr_pricer_tous=True,
    )
    res = api.marche_valorize(req)
    if hasattr(res, "body"):
        res = json.loads(res.body)

    moteur = {str(r.get("titre") or "").strip(): r for r in res.get("prix_manarr", [])}
    print(f"{'code':<8} {'moteur':>14} {'classeur':>14} {'ecart':>10}")
    for code, ref in CODES_REF.items():
        m = moteur.get(code, {})
        prix = m.get("prix_arrondi")
        if prix is None:
            print(f"{code:<8}  (non valorisé)")
            continue
        ecart = round(float(prix) - float(ref), 6)
        print(f"{code:<8} {float(prix):>14.4f} {float(ref):>14.4f} {ecart:>10.6f}")


if __name__ == "__main__":
    main()
