"""Échéancier SQL brut pour 9351."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pricing.data_access import sql_connection  # noqa: E402

with sql_connection() as conn:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sys.columns WHERE object_id = OBJECT_ID('dbo.echeancier_titre') ORDER BY column_id")
    cols = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT * FROM dbo.echeancier_titre WHERE titre = ? ORDER BY num_evenement", "9351")
    rows = cur.fetchall()
    print(f"Found {len(rows)} rows for 9351")
    for row in rows:
        d = dict(zip(cols, row))
        print(d)
