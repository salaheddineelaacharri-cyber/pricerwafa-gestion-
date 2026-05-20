"""Affiche l'échéancier SQL brut de 9500 pour comprendre la précision des amounts."""

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
    print("Columns:", cols)
    cur.execute("SELECT TOP 35 * FROM dbo.echeancier_titre WHERE titre = ? ORDER BY num_evenement", "9500")
    rows = cur.fetchall()
    for row in rows:
        d = dict(zip(cols, row))
        print(d)
