"""Hook construire_tableau_amortissement pour capturer duree_calc_ans / taux_actu_pct
en pleine précision sur 9500."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import obligation_amort_schedule as oa  # noqa: E402

orig = oa.construire_tableau_amortissement
captured: dict = {}


def patched(*args, **kwargs):
    out = orig(*args, **kwargs)
    code = ""
    if isinstance(out, dict):
        code = str(out.get("code") or out.get("CODE") or "").strip()
    if code == "9500":
        # Try to find amort_table internals from the function locals via closure.
        captured["out"] = out
        # The function returns dict with amortissement_table (rows: list of dict)
        rows = out.get("amortissement_table") or out.get("rows") or out.get("amortissement_rows")
        if rows:
            captured["rows_first10"] = rows[:30]
        else:
            captured["keys"] = list(out.keys())
    return out


oa.construire_tableau_amortissement = patched

# Now patch deeper to capture duree_calc_ans, taux_actu_pct
import inspect

src = inspect.getsource(oa)
# find the function and capture its line
print("Hook installed.")

from backend import main as api  # noqa: E402

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

print("\n=== captured for 9500 ===")
import json
print(json.dumps(list(captured.keys())))
out = captured.get("out")
if out:
    for k in sorted(out.keys()):
        v = out[k]
        if isinstance(v, list):
            print(f"  {k}: list[{len(v)}]")
        else:
            print(f"  {k}: {type(v).__name__}: {v!r}"[:200])
