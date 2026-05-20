#!/usr/bin/env python3
"""
Remplace dbo.echeancier_titre a partir d'un Excel : backup horodate, staging, controles, swap transactionnel.

Depuis la racine du projet :
  python scripts/import_echeancier_titre_from_excel.py --excel echeancier_titre1.xlsx
  python scripts/import_echeancier_titre_from_excel.py --excel echeancier_titre1.xlsx --apply-swap

Sans --apply-swap : backup + staging + import + verifications SQL, sans toucher a dbo.echeancier_titre.

Notes importantes :
- Fichier attendu : .xlsx (feuille par defaut : premiere feuille, ex. « Donnees »).
- La PK historique (titre, num_evenement) est supprimee au swap si elle existe : l'Excel contient plusieurs
  lignes par (titre, num_evenement) (versions IM_DATE / tombees). Un index unique
  uq_echeancier_titre_scope sur (titre, num_evenement, date_tombee, im_date_ini, im_date) est cree apres insert.
- Cellules vides pour CAPITAL_RESTANT ou COUPON_BRUT (colonnes NOT NULL en base) sont imputees a 0 avec avertissement.
- IM_DATE / IM_DATE_INI : parse manuel (sans ``pd.to_datetime(..., errors='coerce')``) pour conserver **9999-01-01** en SQL.
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from pricing.data_access import sql_connection

EXCEL_COLS_REQUIRED = (
    "IM_DATE_INI",
    "IM_DATE",
    "TITRE",
    "NUM_EVENEMENT",
    "DATE_DEBUT",
    "DATE_FIN",
    "DATE_TOMBEE",
    "DATE_REGLEMENT",
    "STATUT",
    "CAPITAL_AMORTIS",
    "CAPITAL_RESTANT",
    "TAUX",
    "COUPON_BRUT",
)

SQL_COLS_ORDER = (
    "titre",
    "num_evenement",
    "date_debut",
    "date_fin",
    "date_tombee",
    "date_reglement",
    "statut",
    "capital_amortis",
    "capital_restant",
    "taux",
    "coupon_brut",
    "im_date_ini",
    "im_date",
)

ITUPLE_COLS = (
    "TITRE",
    "NUM_EVENEMENT",
    "DATE_DEBUT",
    "DATE_FIN",
    "DATE_TOMBEE",
    "DATE_REGLEMENT",
    "STATUT",
    "CAPITAL_AMORTIS",
    "CAPITAL_RESTANT",
    "TAUX",
    "COUPON_BRUT",
    "IM_DATE_INI",
    "IM_DATE",
)

# pd.to_datetime(..., errors="coerce") transforme '9999-01-01' en NaT — interdit pour IM_DATE / IM_DATE_INI.
_EXCEL_SERIAL_ORIGIN = date(1899, 12, 30)


def _parse_im_date_cell(val: object) -> date | None:
    """
    Parse IM_DATE_INI / IM_DATE sans pandas.to_datetime (qui met NaT pour 9999-01-01).
    Retourne date(9999, 1, 1) si la source indique cette date (y compris depuis Timestamp Excel).
    """
    if val is None:
        return None
    if isinstance(val, float) and (math.isnan(val) or pd.isna(val)):
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, pd.Timestamp):
        if pd.isna(val):
            return None
        return date(int(val.year), int(val.month), int(val.day))
    if isinstance(val, str):
        s = val.strip()
        if not s or s.lower() in ("nan", "none", "#n/a", "-"):
            return None
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m2 = re.match(r"^(\d{2})/(\d{2})/(\d{4})", s)
        if m2:
            d, mo, y = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
            return date(y, mo, d)
        return None
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        serial = float(val)
        if 1.0 <= serial <= 1_000_000.0:
            try:
                return _EXCEL_SERIAL_ORIGIN + timedelta(days=int(round(serial)))
            except (OverflowError, OSError, ValueError):
                return None
    return None


def _as_date(val: object) -> date | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, pd.Timestamp):
        if pd.isna(val):
            return None
        return val.date()
    ts = pd.to_datetime(val, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.date()


def _as_decimal(val: object, required: bool) -> Decimal | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        if required:
            raise ValueError("valeur numerique obligatoire manquante")
        return None
    if isinstance(val, str) and not val.strip():
        if required:
            raise ValueError("valeur numerique obligatoire manquante")
        return None
    if pd.isna(val):
        if required:
            raise ValueError("valeur numerique obligatoire manquante")
        return None
    try:
        return Decimal(str(val))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"nombre invalide: {val!r}") from exc


def _sql_decimal6(val: object, required: bool) -> Decimal | None:
    """Aligne sur decimal(18,6) SQL Server (evite erreur pyodbc *loses precision*)."""
    d = _as_decimal(val, required=required)
    if d is None:
        return None
    return d.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def _normalize_excel_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().upper() for c in df.columns]
    return df


def load_excel(path: Path, sheet: str | int) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=sheet, engine="openpyxl")
    df = _normalize_excel_columns(df)
    missing = [c for c in EXCEL_COLS_REQUIRED if c not in df.columns]
    if missing:
        raise SystemExit(f"Colonnes Excel manquantes: {missing}. Presentes: {list(df.columns)}")
    return df


def validate_excel_local(df: pd.DataFrame) -> None:
    n = len(df)
    if n == 0:
        raise SystemExit("Excel vide.")
    dup_keys = ["TITRE", "NUM_EVENEMENT", "DATE_TOMBEE", "IM_DATE_INI", "IM_DATE"]
    d = df.duplicated(subset=dup_keys, keep=False)
    if d.any():
        raise SystemExit(f"Doublons Excel sur {dup_keys}: {int(d.sum())} ligne(s).")
    t = df["TITRE"].astype(str).str.replace(r"\.0$", "", regex=True)
    for code in ("5116", "5151"):
        if (t == code).sum() == 0:
            raise SystemExit(f"Aucune ligne pour le code titre {code} dans l'Excel.")
    lens = df["TITRE"].astype(str).str.len()
    if lens.max() > 12:
        raise SystemExit(f"TITRE trop long (>12): max={int(lens.max())}")


def _row_from_ituple(r: tuple) -> tuple:
    """Construit une ligne INSERT depuis une ligne ``itertuples`` (meme ordre que ITUPLE_COLS)."""
    m = dict(zip(ITUPLE_COLS, r))
    titre = str(m["TITRE"]).strip()
    if titre.endswith(".0") and titre[:-2].isdigit():
        titre = titre[:-2]
    if len(titre) > 12:
        raise ValueError(f"titre trop long: {titre!r}")
    num_ev = int(m["NUM_EVENEMENT"])
    statut = str(m["STATUT"]).strip()
    if not statut:
        raise ValueError("STATUT vide")
    statut = statut[0]
    return (
        titre,
        num_ev,
        _as_date(m["DATE_DEBUT"]),
        _as_date(m["DATE_FIN"]),
        _as_date(m["DATE_TOMBEE"]),
        _as_date(m["DATE_REGLEMENT"]),
        statut,
        _sql_decimal6(m["CAPITAL_AMORTIS"], required=False),
        (_sql_decimal6(m["CAPITAL_RESTANT"], required=False) or Decimal("0")),
        (_sql_decimal6(m["TAUX"], required=False) or Decimal("0")),
        (_sql_decimal6(m["COUPON_BRUT"], required=False) or Decimal("0")),
        _parse_im_date_cell(m["IM_DATE_INI"]),
        _parse_im_date_cell(m["IM_DATE"]),
    )


def insert_staging(cur, df: pd.DataFrame, batch: int = 2500) -> None:
    placeholders = ", ".join("?" * len(SQL_COLS_ORDER))
    cols_sql = ", ".join(SQL_COLS_ORDER)
    sql = f"INSERT INTO dbo.echeancier_titre_staging ({cols_sql}) VALUES ({placeholders})"
    cur.fast_executemany = True
    ordered = df[list(ITUPLE_COLS)]
    rows: list[tuple] = []
    n_done = 0
    for tup in ordered.itertuples(index=False, name=None):
        rows.append(_row_from_ituple(tup))
        if len(rows) >= batch:
            cur.executemany(sql, rows)
            n_done += len(rows)
            print(f"   ... {n_done} lignes inserees en staging", flush=True)
            rows.clear()
    if rows:
        cur.executemany(sql, rows)
        n_done += len(rows)
        print(f"   ... {n_done} lignes inserees en staging (fin)", flush=True)


UQ_SCHEDULE_NAME = "uq_echeancier_titre_scope"


def _drop_legacy_pk_if_present(cur) -> bool:
    """Retire la PK (titre, num_evenement) si elle existe — incompatible avec plusieurs versions d'echeancier."""
    cur.execute(
        """
        SELECT COUNT(*) FROM sys.key_constraints
        WHERE parent_object_id = OBJECT_ID(N'dbo.echeancier_titre', N'U')
          AND name = N'pk_echeancier_titre';
        """
    )
    if cur.fetchone()[0] == 0:
        return False
    cur.execute("ALTER TABLE dbo.echeancier_titre DROP CONSTRAINT pk_echeancier_titre;")
    return True


def _drop_schedule_unique_if_present(cur) -> None:
    cur.execute(
        f"""
        IF EXISTS (
            SELECT 1 FROM sys.indexes
            WHERE object_id = OBJECT_ID(N'dbo.echeancier_titre', N'U')
              AND name = N'{UQ_SCHEDULE_NAME}'
        )
            DROP INDEX {UQ_SCHEDULE_NAME} ON dbo.echeancier_titre;
        """
    )


def _create_schedule_unique_index(cur) -> None:
    cur.execute(
        f"""
        CREATE UNIQUE NONCLUSTERED INDEX {UQ_SCHEDULE_NAME}
        ON dbo.echeancier_titre (
            titre,
            num_evenement,
            date_tombee,
            im_date_ini,
            im_date
        );
        """
    )


def run_post_swap_checks(cur) -> None:
    print("\n--- Post-swap : filtres valorisation 06/03/2026 ---")
    for code, label in (("5116", "5116"), ("5151", "5151")):
        cur.execute(
            """
            SELECT titre, num_evenement, date_tombee, im_date_ini, im_date, statut,
                   capital_amortis, capital_restant, taux, coupon_brut
            FROM dbo.echeancier_titre
            WHERE titre = ?
              AND im_date_ini <= ?
              AND im_date > ?
            ORDER BY date_tombee
            """,
            (code, date(2026, 3, 6), date(2026, 3, 6)),
        )
        r = cur.fetchall()
        print(f"  {label}: {len(r)} ligne(s) IM_DATE_INI <= 2026-03-06 AND IM_DATE > 2026-03-06")
        for line in r[:5]:
            print("   ", line)
        if len(r) > 5:
            print("    ...")


def main() -> None:
    ap = argparse.ArgumentParser(description="Import dbo.echeancier_titre depuis Excel (backup + staging + swap).")
    ap.add_argument("--excel", type=Path, default=Path("echeancier_titre1.xlsx"), help="Chemin du fichier .xlsx")
    ap.add_argument("--sheet", default=0, help="Feuille Excel (indice ou nom). Defaut: 0")
    ap.add_argument(
        "--apply-swap",
        action="store_true",
        help="Apres controles staging: DELETE echeancier_titre + INSERT depuis staging (transaction).",
    )
    args = ap.parse_args()
    path = args.excel.resolve() if args.excel.is_absolute() else (ROOT / args.excel).resolve()
    if not path.exists():
        raise SystemExit(f"Fichier introuvable: {path}")

    print(f"Lecture Excel: {path}", flush=True)
    df = load_excel(path, args.sheet)
    validate_excel_local(df)
    n_excel = len(df)
    na_cr = int(df["CAPITAL_RESTANT"].isna().sum())
    na_cb = int(df["COUPON_BRUT"].isna().sum())
    na_tx = int(df["TAUX"].isna().sum())
    if na_cr or na_cb or na_tx:
        print(
            f"Attention: cellules vides imputees a 0 en SQL (NOT NULL) — "
            f"CAPITAL_RESTANT={na_cr}, COUPON_BRUT={na_cb}, TAUX={na_tx}",
            flush=True,
        )
    print(f"Excel: {n_excel} lignes, feuille={args.sheet!r}", flush=True)

    suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"echeancier_titre_backup_{suffix}"
    backup_table = f"dbo.{backup_name}"

    with sql_connection() as conn:
        conn.autocommit = False
        cur = conn.cursor()

        print(f"\n1) Backup: SELECT * INTO {backup_table} FROM dbo.echeancier_titre")
        cur.execute(
            f"""
            IF OBJECT_ID(N'dbo.{backup_name}', N'U') IS NOT NULL
                DROP TABLE dbo.{backup_name};
            """
        )
        cur.execute(f"SELECT * INTO dbo.{backup_name} FROM dbo.echeancier_titre")
        conn.commit()
        cur.execute(f"SELECT COUNT(*) FROM dbo.{backup_name}")
        n_bak = cur.fetchone()[0]
        print(f"   Backup OK: {n_bak} ligne(s) dans {backup_table}")

        print("\n2) Staging: DROP IF EXISTS + SELECT TOP 0 * INTO dbo.echeancier_titre_staging ...")
        cur.execute(
            """
            IF OBJECT_ID(N'dbo.echeancier_titre_staging', N'U') IS NOT NULL
                DROP TABLE dbo.echeancier_titre_staging;
            """
        )
        cur.execute(
            """
            SELECT TOP 0 *
            INTO dbo.echeancier_titre_staging
            FROM dbo.echeancier_titre;
            """
        )
        conn.commit()

        print("\n3) Import Excel -> staging ...")
        insert_staging(cur, df)
        conn.commit()

        cur.execute("SELECT COUNT(*) FROM dbo.echeancier_titre_staging")
        n_st = cur.fetchone()[0]
        print(f"\n4) COUNT staging = {n_st} (Excel = {n_excel})")
        if n_st != n_excel:
            raise SystemExit("ECHEC: nombre de lignes staging != Excel.")

        cur.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT titre, num_evenement, date_tombee, im_date_ini, im_date, COUNT(*) AS c
                FROM dbo.echeancier_titre_staging
                GROUP BY titre, num_evenement, date_tombee, im_date_ini, im_date
                HAVING COUNT(*) > 1
            ) x;
            """
        )
        ndup = cur.fetchone()[0]
        print(f"   Doublons SQL (titre, num_evenement, date_tombee, im_date_ini, im_date): {ndup}")
        if ndup:
            raise SystemExit("ECHEC: doublons detectes en staging.")

        print("\n5) Echantillon staging TOP 20:")
        cur.execute("SELECT TOP 20 * FROM dbo.echeancier_titre_staging ORDER BY titre, num_evenement")
        cols = [d[0] for d in cur.description]
        for row in cur.fetchall():
            print("  ", dict(zip(cols, row)))

        print("\n6) Codes 5116 et 5151 dans staging:")
        cur.execute(
            """
            SELECT titre, COUNT(*) AS n
            FROM dbo.echeancier_titre_staging
            WHERE titre IN (N'5116', N'5151')
            GROUP BY titre
            ORDER BY titre;
            """
        )
        found = {str(r[0]): int(r[1]) for r in cur.fetchall()}
        for c in ("5116", "5151"):
            if c not in found:
                raise SystemExit(f"ECHEC: pas de lignes staging pour titre {c}")
            print(f"   titre {c}: {found[c]} ligne(s)")

        cur.execute(
            """
            SELECT TOP 50 *
            FROM dbo.echeancier_titre_staging
            WHERE titre IN (N'5116', N'5151')
            ORDER BY titre, im_date_ini, date_tombee;
            """
        )
        print("   (50 premieres lignes 5116/5151 tri titre, im_date_ini, date_tombee)")
        cols = [d[0] for d in cur.description]
        for row in cur.fetchall():
            print("  ", dict(zip(cols, row)))

        if not args.apply_swap:
            print(
                "\nOK (sans swap). dbo.echeancier_titre inchangé. "
                "Relancez avec --apply-swap pour DELETE + INSERT depuis staging."
            )
            return

        cols_csv = ", ".join(SQL_COLS_ORDER)
        print("\n7) Swap transactionnel: PK legacy + DELETE + INSERT + index unique 5 colonnes ...")
        try:
            dropped = _drop_legacy_pk_if_present(cur)
            if dropped:
                print("   Contrainte pk_echeancier_titre (titre, num_evenement) supprimee.", flush=True)
            _drop_schedule_unique_if_present(cur)
            cur.execute("DELETE FROM dbo.echeancier_titre;")
            cur.execute(
                f"""
                INSERT INTO dbo.echeancier_titre ({cols_csv})
                SELECT {cols_csv}
                FROM dbo.echeancier_titre_staging;
                """
            )
            _create_schedule_unique_index(cur)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise SystemExit(f"ECHEC swap (rollback): {exc}") from exc

        cur.execute("SELECT COUNT(*) FROM dbo.echeancier_titre")
        n_main = cur.fetchone()[0]
        print(f"   dbo.echeancier_titre contient maintenant {n_main} ligne(s).")
        run_post_swap_checks(cur)


if __name__ == "__main__":
    main()
