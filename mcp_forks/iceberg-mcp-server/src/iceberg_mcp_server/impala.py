"""Impala connection for catalog SQL. The server does not author statements."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_JWT_PATH = Path("/tmp/jwt")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _jwt_access_token() -> str:
    configured = os.getenv("IMPALA_JWT", "").strip()
    if configured:
        return configured
    if not _JWT_PATH.is_file():
        return ""
    raw = _JWT_PATH.read_text(encoding="utf-8").strip()
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(payload, dict):
        token = payload.get("access_token") or payload.get("token") or ""
        return str(token).strip()
    return ""


def _host_and_port() -> tuple[str, int, bool]:
    """Bare coordinator host. A pasted http://host/ URL is not a DNS name."""
    raw = os.getenv("IMPALA_HOST", "localhost").strip() or "localhost"
    port_env = os.getenv("IMPALA_PORT", "").strip()
    if "://" not in raw:
        host = raw.split("/", 1)[0]
        return host, int(port_env or "21050"), False
    parsed = urlparse(raw)
    host = parsed.hostname or "localhost"
    if port_env:
        port = int(port_env)
    elif parsed.port:
        port = parsed.port
    elif parsed.scheme == "https":
        port = 443
    else:
        port = 21050
    return host, port, True


def connection_kwargs() -> dict[str, Any]:
    """Arguments for impyla.connect. Port 443 is the CDW TLS HTTP endpoint."""
    host, port, from_url = _host_and_port()
    tls = port == 443 or from_url
    use_http = _env_bool("IMPALA_USE_HTTP_TRANSPORT", tls)
    use_ssl = _env_bool("IMPALA_USE_SSL", port == 443)
    user = os.getenv("IMPALA_USER", "").strip()
    password = os.getenv("IMPALA_PASSWORD", "").strip()
    token = _jwt_access_token()
    auth = os.getenv("IMPALA_AUTH_MECHANISM", "").strip()
    if not auth:
        if token and use_http:
            auth = "JWT"
        elif user and password:
            auth = "LDAP"
        else:
            auth = "NOSASL"
    kwargs: dict[str, Any] = {
        "host": host,
        "port": port,
        "database": os.getenv("IMPALA_DATABASE", "default"),
        "auth_mechanism": auth,
        "use_ssl": use_ssl,
        "use_http_transport": use_http,
    }
    if use_http:
        kwargs["http_path"] = os.getenv("IMPALA_HTTP_PATH", "cliservice")
    if auth == "JWT":
        kwargs["jwt"] = token
    else:
        if user:
            kwargs["user"] = user
        if password:
            kwargs["password"] = password
    return kwargs


def _connect():
    from impala.dbapi import connect

    return connect(**connection_kwargs())


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
