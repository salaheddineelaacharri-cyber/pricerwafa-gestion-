from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator

import pandas as pd

LOGGER = logging.getLogger("pricer.data_access")

DEFAULT_SQL_SERVER = r"localhost\SQLEXPRESS"
DEFAULT_SQL_DATABASE = "obligation"


class SqlDataAccessError(RuntimeError):
    """Erreur explicite pour les indisponibilites SQL Server runtime."""


def _odbc_driver() -> str:
    try:
        import pyodbc
    except ImportError as exc:  # pragma: no cover - depend de l'environnement runtime
        raise SqlDataAccessError(
            "Le module pyodbc est requis pour charger les donnees depuis SQL Server. "
            "Installez pyodbc ou ajoutez-le a l'environnement Python."
        ) from exc

    preferred = os.environ.get("PRICER_SQL_ODBC_DRIVER", "").strip()
    if preferred:
        return preferred

    installed = set(pyodbc.drivers())
    for candidate in (
        "ODBC Driver 17 for SQL Server",
        "ODBC Driver 18 for SQL Server",
        "SQL Server Native Client 11.0",
        "SQL Server",
    ):
        if candidate in installed:
            return candidate
    raise SqlDataAccessError(
        "Aucun driver ODBC SQL Server compatible n'a ete trouve. "
        "Installez ODBC Driver 18/17 for SQL Server ou renseignez PRICER_SQL_ODBC_DRIVER."
    )


def connection_string() -> str:
    explicit = os.environ.get("PRICER_SQL_CONNECTION_STRING", "").strip()
    if explicit:
        return explicit

    server = os.environ.get("PRICER_SQL_SERVER", DEFAULT_SQL_SERVER).strip() or DEFAULT_SQL_SERVER
    database = os.environ.get("PRICER_SQL_DATABASE", DEFAULT_SQL_DATABASE).strip() or DEFAULT_SQL_DATABASE
    driver = _odbc_driver()
    trust_cert = os.environ.get("PRICER_SQL_TRUST_SERVER_CERTIFICATE", "yes").strip() or "yes"
    encrypt = os.environ.get("PRICER_SQL_ENCRYPT", "no").strip() or "no"
    timeout = os.environ.get("PRICER_SQL_TIMEOUT", "5").strip() or "5"
    return _make_connection_string(driver, server, database, encrypt, trust_cert, timeout)


def _make_connection_string(
    driver: str,
    server: str,
    database: str,
    encrypt: str,
    trust_cert: str,
    timeout: str,
) -> str:
    return (
        f"DRIVER={{{driver}}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        "Trusted_Connection=yes;"
        f"Encrypt={encrypt};"
        f"TrustServerCertificate={trust_cert};"
        f"Connection Timeout={timeout};"
    )


def _server_candidates() -> list[str]:
    configured = os.environ.get("PRICER_SQL_SERVER", DEFAULT_SQL_SERVER).strip() or DEFAULT_SQL_SERVER
    candidates = [configured]
    computer = os.environ.get("COMPUTERNAME", "").strip()
    if computer:
        candidates.append(fr"{computer}\SQLEXPRESS")
    candidates.append(r"DESKTOP-5K88T8O\SQLEXPRESS")
    out: list[str] = []
    for server in candidates:
        if server and server not in out:
            out.append(server)
    return out


@contextmanager
def sql_connection() -> Iterator[Any]:
    try:
        import pyodbc
    except ImportError as exc:  # pragma: no cover
        raise SqlDataAccessError(
            "pyodbc est indisponible: impossible de se connecter a SQL Server."
        ) from exc

    driver = _odbc_driver()
    database = os.environ.get("PRICER_SQL_DATABASE", DEFAULT_SQL_DATABASE).strip() or DEFAULT_SQL_DATABASE
    trust_cert = os.environ.get("PRICER_SQL_TRUST_SERVER_CERTIFICATE", "yes").strip() or "yes"
    encrypt = os.environ.get("PRICER_SQL_ENCRYPT", "no").strip() or "no"
    timeout = os.environ.get("PRICER_SQL_TIMEOUT", "5").strip() or "5"
    last_exc: Exception | None = None
    try:
        for server in _server_candidates():
            cs = _make_connection_string(driver, server, database, encrypt, trust_cert, timeout)
            try:
                LOGGER.info("Connexion SQL Server: %s / %s", server, database)
                conn = pyodbc.connect(cs)
                break
            except Exception as exc:
                last_exc = exc
                LOGGER.warning("Connexion SQL Server echouee sur %s: %s", server, exc)
        else:
            raise last_exc or RuntimeError("Aucun serveur SQL candidat")
    except Exception as exc:
        LOGGER.exception("Connexion SQL Server indisponible")
        raise SqlDataAccessError(
            "SQL Server est indisponible ou inaccessible "
            f"({', '.join(_server_candidates())} / {database}): {exc}"
        ) from exc
    try:
        yield conn
    finally:
        conn.close()


def read_sql_dataframe(query: str, params: tuple[Any, ...] | None = None) -> pd.DataFrame:
    try:
        with sql_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params or ())
            columns = [col[0] for col in cursor.description]
            rows = cursor.fetchall()
            df = pd.DataFrame.from_records(rows, columns=columns)
    except SqlDataAccessError:
        raise
    except Exception as exc:
        LOGGER.exception("Lecture SQL impossible")
        raise SqlDataAccessError(f"Lecture SQL impossible: {exc}") from exc
    df.columns = [str(c).strip().upper() for c in df.columns]
    return df


def charger_referentiel_titre() -> pd.DataFrame:
    df = read_sql_dataframe("SELECT * FROM dbo.referentiel_titre ORDER BY code")
    LOGGER.info("referentiel_titre charge: %s lignes", len(df))
    return df


def _codes_sql_normalises(codes: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    out: list[str] = []
    for code in codes:
        s = str(code or "").strip()
        if s.endswith(".0") and s[:-2].isdigit():
            s = s[:-2]
        if s and s not in out:
            out.append(s)
    return out


def charger_referentiel_titre_codes(codes: list[str] | tuple[str, ...] | set[str]) -> pd.DataFrame:
    codes_n = _codes_sql_normalises(codes)
    if not codes_n:
        return charger_referentiel_titre()
    placeholders = ", ".join("?" for _ in codes_n)
    df = read_sql_dataframe(
        f"SELECT * FROM dbo.referentiel_titre WHERE code IN ({placeholders}) ORDER BY code",
        tuple(codes_n),
    )
    LOGGER.info("referentiel_titre charge filtre: %s lignes / %s code(s)", len(df), len(codes_n))
    return df


def charger_echeancier_titre() -> pd.DataFrame:
    df = read_sql_dataframe(
        """
        SELECT
            titre AS CODE,
            titre AS TITRE,
            num_evenement AS NUM_EVENEMENT,
            date_debut AS DATE_DEBUT,
            date_fin AS DATE_FIN,
            date_tombee AS DATE_TOMBEE,
            date_reglement AS DATE_REGLEMENT,
            statut AS STATUT,
            capital_amortis AS CAPITAL_AMORTIS,
            capital_amortis AS AMORTISSEMENT,
            capital_restant AS CAPITAL_RESTANT,
            taux AS TAUX,
            coupon_brut AS COUPON_BRUT,
            coupon_brut AS COUPON,
            coupon_brut AS INTERET,
            COALESCE(capital_amortis, 0) + COALESCE(coupon_brut, 0) AS FLUX,
            CONVERT(varchar(10), im_date_ini, 23) AS IM_DATE_INI,
            CONVERT(varchar(10), im_date, 23) AS IM_DATE
        FROM dbo.echeancier_titre
        ORDER BY titre, num_evenement
        """
    )
    LOGGER.info("echeancier_titre charge: %s lignes", len(df))
    return df


def charger_echeancier_titre_codes(codes: list[str] | tuple[str, ...] | set[str]) -> pd.DataFrame:
    codes_n = _codes_sql_normalises(codes)
    if not codes_n:
        return charger_echeancier_titre()
    placeholders = ", ".join("?" for _ in codes_n)
    df = read_sql_dataframe(
        f"""
        SELECT
            titre AS CODE,
            titre AS TITRE,
            num_evenement AS NUM_EVENEMENT,
            date_debut AS DATE_DEBUT,
            date_fin AS DATE_FIN,
            date_tombee AS DATE_TOMBEE,
            date_reglement AS DATE_REGLEMENT,
            statut AS STATUT,
            capital_amortis AS CAPITAL_AMORTIS,
            capital_amortis AS AMORTISSEMENT,
            capital_restant AS CAPITAL_RESTANT,
            taux AS TAUX,
            coupon_brut AS COUPON_BRUT,
            coupon_brut AS COUPON,
            coupon_brut AS INTERET,
            COALESCE(capital_amortis, 0) + COALESCE(coupon_brut, 0) AS FLUX,
            CONVERT(varchar(10), im_date_ini, 23) AS IM_DATE_INI,
            CONVERT(varchar(10), im_date, 23) AS IM_DATE
        FROM dbo.echeancier_titre
        WHERE titre IN ({placeholders})
        ORDER BY titre, num_evenement
        """,
        tuple(codes_n),
    )
    LOGGER.info("echeancier_titre charge filtre: %s lignes / %s code(s)", len(df), len(codes_n))
    return df


def charger_referentiel_et_echeancier() -> tuple[pd.DataFrame, pd.DataFrame]:
    return charger_referentiel_titre(), charger_echeancier_titre()


def charger_referentiel_et_echeancier_codes(
    codes: list[str] | tuple[str, ...] | set[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    return charger_referentiel_titre_codes(codes), charger_echeancier_titre_codes(codes)


def diagnostic_sources_sql() -> dict[str, Any]:
    try:
        counts = read_sql_dataframe(
            """
            SELECT 'referentiel_titre' AS TABLE_NAME, COUNT(*) AS NB FROM dbo.referentiel_titre
            UNION ALL
            SELECT 'echeancier_titre', COUNT(*) FROM dbo.echeancier_titre
            UNION ALL
            SELECT 'histo_courbe_taux', COUNT(*) FROM dbo.histo_courbe_taux
            """
        )
        return {
            "ok": True,
            "source": "sql_server",
            "tables": counts.to_dict(orient="records"),
            "feuille_referentiel": "dbo.referentiel_titre",
            "feuille_echeancier": "dbo.echeancier_titre",
            "sheet_names": ["dbo.referentiel_titre", "dbo.echeancier_titre"],
        }
    except Exception as exc:
        return {
            "ok": False,
            "source": "sql_server",
            "error": str(exc),
            "sheet_names": [],
            "feuille_referentiel": None,
            "feuille_echeancier": None,
        }


def charger_histo_courbe_taux(courbe: str) -> pd.DataFrame:
    courbe_norm = (courbe or "MAR_JJ").strip() or "MAR_JJ"
    df = read_sql_dataframe(
        """
        SELECT courbe, date_courbe, maturite, valeur_maturite, valeur_taux, bid_taux, ask_taux
        FROM dbo.histo_courbe_taux
        WHERE courbe = ?
        ORDER BY date_courbe, valeur_maturite
        """,
        (courbe_norm,),
    )
    LOGGER.info("histo_courbe_taux charge: %s lignes pour courbe=%s", len(df), courbe_norm)
    return df


def liste_titres_referentiel() -> tuple[list[dict[str, Any]], str]:
    df = read_sql_dataframe(
        """
        SELECT
            code AS titre,
            date_maj AS date,
            CAST(NULL AS decimal(18, 6)) AS valo,
            CAST(NULL AS decimal(18, 6)) AS prix_mr,
            CAST(NULL AS decimal(18, 6)) AS ecart
        FROM dbo.referentiel_titre
        ORDER BY code
        """
    )
    out: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        titre = str(row.get("TITRE") or "").strip()
        if not titre:
            continue
        d = row.get("DATE")
        out.append(
            {
                "titre": titre,
                "date": d.strftime("%d/%m/%Y") if hasattr(d, "strftime") else (str(d) if d is not None else None),
                "valo": None,
                "prix_mr": None,
                "ecart": None,
            }
        )
    return out, "dbo.referentiel_titre"


def prix_mr_map() -> dict[str, float]:
    # La base SQL fournie ne contient pas l'ancienne colonne Feuil1.valo / Prix MR.
    return {}
