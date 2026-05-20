"""Affiche la catégorie SQL pour 9500, 9351, 9424, 101006, 9651."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pricing.data_access import sql_connection  # noqa: E402

CODES = ("9500", "9351", "9424", "101006", "9651")
with sql_connection() as conn:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sys.columns WHERE object_id = OBJECT_ID('dbo.referentiel_titre') ORDER BY column_id")
    cols = [r[0] for r in cur.fetchall()]
    print("Columns:", cols)
    for code in CODES:
        cur.execute("SELECT TOP 1 * FROM dbo.referentiel_titre WHERE code = ?", code)
        row = cur.fetchone()
        if row is None:
            print(f"{code}: NOT FOUND")
            continue
        d = dict(zip(cols, row))
        keep = {k: d.get(k) for k in cols if any(s in k.lower() for s in ("categori", "periodicite", "base_calcul", "methode", "spread", "type", "fix_rev", "code_maro", "duree"))}
        print(f"\n{code}: {keep}")
