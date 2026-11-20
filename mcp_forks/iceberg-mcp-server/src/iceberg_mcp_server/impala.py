"""Impala connection for catalog SQL. The server does not author statements."""

from __future__ import annotations

import os
from typing import Any


def _connect():
    from impala.dbapi import connect

    return connect(
        host=os.getenv("IMPALA_HOST", "localhost"),
        port=int(os.getenv("IMPALA_PORT", "21050")),
        user=os.getenv("IMPALA_USER", ""),
        password=os.getenv("IMPALA_PASSWORD", ""),
        database=os.getenv("IMPALA_DATABASE", "default"),
        auth_mechanism=os.getenv("IMPALA_AUTH_MECHANISM", "NOSASL"),
    )


_QUERY_PREFIXES = ("select", "with", "show", "describe")


def _prefix(sql: str) -> str:
    return sql.split(None, 1)[0].lower() if sql.split(None, 1) else ""


def _fetch(cursor) -> list[dict[str, Any]]:
    columns = [item[0] for item in cursor.description or []]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def query_rows(sql: str) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute(sql)
        return _fetch(cursor)
    finally:
        conn.close()


def execute_dml(sql: str) -> str:
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute(sql)
        return "ok"
    finally:
        conn.close()


def run_statements(statements: list[str]) -> list[dict[str, Any]]:
    """Run every statement on one Impala connection, in order."""
    conn = _connect()
    try:
        cursor = conn.cursor()
        results: list[dict[str, Any]] = []
        for sql in statements:
            cursor.execute(sql)
            if _prefix(sql) in _QUERY_PREFIXES:
                results.append({"kind": "read", "rows": _fetch(cursor)})
            else:
                results.append({"kind": "write", "message": "ok"})
        return results
    finally:
        conn.close()
