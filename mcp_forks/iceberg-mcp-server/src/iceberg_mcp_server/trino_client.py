"""Trino connection for catalog SQL. The server does not author statements."""

from __future__ import annotations

import os
from typing import Any

_QUERY_PREFIXES = ("select", "with", "show", "describe")


def connection_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "host": os.getenv("TRINO_HOST", "localhost"),
        "port": int(os.getenv("TRINO_PORT", "8080")),
        "user": os.getenv("TRINO_USER", "trino"),
        "catalog": os.getenv("TRINO_CATALOG", "iceberg"),
        "schema": os.getenv("TRINO_SCHEMA", os.getenv("TRINO_DATABASE", "default")),
        "http_scheme": os.getenv("TRINO_HTTP_SCHEME", "http"),
    }
    password = os.getenv("TRINO_PASSWORD", "").strip()
    if password:
        from trino.auth import BasicAuthentication

        kwargs["auth"] = BasicAuthentication(kwargs["user"], password)
    return kwargs


def _connect():
    from trino.dbapi import connect

    return connect(**connection_kwargs())


def _prefix(sql: str) -> str:
    return sql.split(None, 1)[0].lower() if sql.split(None, 1) else ""


def _fetch(cursor) -> list[dict[str, Any]]:
    columns = [item[0] for item in cursor.description or []]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _failure(exc: BaseException) -> RuntimeError:
    host = os.getenv("TRINO_HOST", "localhost")
    port = os.getenv("TRINO_PORT", "8080")
    user = os.getenv("TRINO_USER", "trino")
    return RuntimeError(
        f"{type(exc).__name__}: {exc} (host={host} port={port} user={user})"
    )


def run_statements(statements: list[str]) -> list[dict[str, Any]]:
    conn = None
    try:
        conn = _connect()
        cursor = conn.cursor()
        results: list[dict[str, Any]] = []
        for sql in statements:
            cursor.execute(sql)
            if _prefix(sql) in _QUERY_PREFIXES:
                results.append({"kind": "read", "rows": _fetch(cursor)})
            else:
                results.append({"kind": "write", "message": "ok"})
        return results
    except Exception as exc:
        raise _failure(exc) from exc
    finally:
        if conn is not None:
            conn.close()
