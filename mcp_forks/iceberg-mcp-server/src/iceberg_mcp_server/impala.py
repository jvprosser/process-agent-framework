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


def query_rows(sql: str) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute(sql)
        columns = [item[0] for item in cursor.description or []]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
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
