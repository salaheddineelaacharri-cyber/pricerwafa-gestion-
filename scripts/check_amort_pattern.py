"""Vérifie le pattern d'amortissement SQL pour un set de codes."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pricing.data_access import sql_connection  # noqa: E402

CODES = ("9748", "9580", "201937", "9538", "5151", "9500", "9351", "9424")

with sql_connection() as conn:
    cur = conn.cursor()
    for code in CODES:
        cur.execute("SELECT capital_amortis FROM dbo.echeancier_titre WHERE titre = ? ORDER BY num_evenement", code)
        rows = [float(r[0]) for r in cur.fetchall()]
        pos = [a for a in rows if a > 1e-6]
        if not pos:
            print(f"{code}: no positive amorts (n={len(rows)})")
            continue
        all_strict = all(abs(a - pos[0]) < 1e-9 for a in pos)
        last_diff = abs(pos[-1] - pos[0]) if len(pos) > 1 else 0.0
        print(f"{code}: n_pos={len(pos)} first={pos[0]:.4f} last={pos[-1]:.4f} all_strict_equal={all_strict} last_diff={last_diff:.4f}")
