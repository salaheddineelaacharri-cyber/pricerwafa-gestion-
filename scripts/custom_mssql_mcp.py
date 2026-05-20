#!/usr/bin/env python3
from __future__ import annotations

import os
from typing import Any

import pyodbc
from mcp.server.fastmcp import FastMCP

MAX_ROWS_RETURNED = 500

mcp = FastMCP("obligation-db")


def get_connection():
    conn_str = (
        "DRIVER={ODBC Driver 17 for SQL Server};"
        "SERVER=DESKTOP-5K88T8O\SQLEXPRESS;"
        "DATABASE=obligation;"
        "Trusted_Connection=yes;"
        "Encrypt=no;"
        "TrustServerCertificate=yes;"
    )
    return pyodbc.connect(conn_str)


def serialize_value(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


@mcp.tool()
def execute_sql(query: str) -> str:
    """
    Execute a SQL query on SQL Server database obligation.
    Returns SELECT results up to 500 rows.
    """
    conn = None
    cursor = None

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(query)

        if cursor.description:
            columns = [col[0] for col in cursor.description]
            rows = cursor.fetchmany(MAX_ROWS_RETURNED)

            if not rows:
                return "Status: success\nRows returned: 0\n(no rows)"

            data = [[serialize_value(v) for v in row] for row in rows]

            header = " | ".join(columns)

            lines = [
                "Status: success",
                f"Rows returned: {len(data)}",
                "",
                header,
                "-" * max(20, len(header)),
            ]

            for row in data:
                lines.append(" | ".join(str(v) for v in row))

            return "\n".join(lines)

        conn.commit()
        return f"Status: success\nRows affected: {cursor.rowcount}"

    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return f"❌ Error: {e}"

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


if __name__ == "__main__":
    mcp.run(transport="stdio")
