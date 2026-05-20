"""Comparaison plein-précision (sans arrondi 2 décimales) sur les 5 codes de référence.

Appelle ``marche_valorize`` puis lit ``Prix arrondi`` (6 décimales) dans ``lignes``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import main as api  # noqa: E402


CODES_REF = {
    "101006": 100577.6813,
    "9651": 101096.3306,
    "9424": 75571.4673,
    "9500": 98542.8764,
    "9351": 47586.4322,
}


def _norm(c) -> str:
    s = str(c or "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


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

    rows = res.get("rows", []) or res.get("lignes", [])
    by_code: dict[str, dict] = {}
    for r in rows:
        c = _norm(r.get("CODE") or r.get("code") or r.get("titre"))
        if c:
            by_code[c] = r

    print(f"{'code':<8} {'moteur (6 dec)':>18} {'classeur':>14} {'ecart':>14}")
    for code, ref in CODES_REF.items():
        r = by_code.get(code)
        if not r:
            print(f"{code:<8}  (introuvable)")
            continue
        pa = r.get("Prix arrondi")
        if pa is None:
            pa = r.get("prix_arrondi") or r.get("Prix clean")
        if pa is None:
            print(f"{code:<8}  (pas de prix)")
            continue
        ecart = float(pa) - float(ref)
        print(f"{code:<8} {float(pa):>18.6f} {float(ref):>14.4f} {ecart:>14.6f}")


if __name__ == "__main__":
    main()
