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


def _host_and_port() -> tuple[str, int]:
    """Bare coordinator host. A pasted http://host/ URL is not a DNS name."""
    raw = os.getenv("IMPALA_HOST", "").strip()
    port_env = os.getenv("IMPALA_PORT", "").strip()
    if "://" not in raw:
        host = (raw or "coordinator-default-impala.example.com").split("/", 1)[0]
        return host, int(port_env or "443")
    parsed = urlparse(raw)
    host = parsed.hostname or "coordinator-default-impala.example.com"
    if port_env:
        port = int(port_env)
    elif parsed.port:
        port = parsed.port
    else:
        port = 443
    return host, port


def connection_kwargs() -> dict[str, Any]:
    """Same Impala settings the claims MCP server read from the environment."""
    host, port = _host_and_port()
    auth = os.getenv("IMPALA_AUTH_MECHANISM", "LDAP").strip() or "LDAP"
    kwargs: dict[str, Any] = {
        "host": host,
        "port": port,
        "database": os.getenv("IMPALA_DATABASE", "default"),
        "auth_mechanism": auth,
        "use_http_transport": _env_bool("IMPALA_USE_HTTP_TRANSPORT", True),
        "http_path": os.getenv("IMPALA_HTTP_PATH", "cliservice"),
        "use_ssl": True,
    }
    if auth.upper() == "JWT":
        kwargs["auth_mechanism"] = "JWT"
        kwargs["jwt"] = _jwt_access_token()
        return kwargs
    kwargs["user"] = os.getenv("IMPALA_USER", "username")
    kwargs["password"] = os.getenv("IMPALA_PASSWORD", "password")
    return kwargs


def _connect():
    from impala.dbapi import connect

    _guard_http_client()
    return connect(**connection_kwargs())


def _guard_http_client() -> dict[str, str]:
    """impyla's HTTP close() raises if the socket was never opened, hiding the real error."""
    from impala._thrift_api import ImpalaHttpClient

    captured = getattr(ImpalaHttpClient, "_paf_capture", None)
    if not isinstance(captured, dict):
        captured = {}
        ImpalaHttpClient._paf_capture = captured
    if getattr(ImpalaHttpClient, "_paf_guarded", False):
        return captured

    def close(self):
        http = getattr(self, "_ImpalaHttpClient__http", None)
        if http is not None:
            http.close()
        self._ImpalaHttpClient__http = None
        self._ImpalaHttpClient__http_response = None

    original_flush = ImpalaHttpClient.flush

    def flush(self):
        try:
            return original_flush(self)
        except Exception as exc:
            body = getattr(exc, "body", b"")
            if isinstance(body, bytes):
                body = body.decode("utf-8", "replace")
            text = f"{type(exc).__name__}: {exc}"
            if body:
                text += f" body={body[:500]}"
            box = getattr(ImpalaHttpClient, "_paf_capture", None)
            if isinstance(box, dict):
                box["detail"] = text
            raise

    ImpalaHttpClient.close = close
    ImpalaHttpClient.flush = flush
    ImpalaHttpClient._paf_guarded = True
    return captured


def _failure(exc: BaseException, captured: dict[str, str]) -> RuntimeError:
    host, port = _host_and_port()
    auth = str(connection_kwargs().get("auth_mechanism") or "")
    message = f"{type(exc).__name__}: {exc}"
    detail = captured.get("detail", "")
    if detail and detail not in message:
        message = f"{message}; {detail}"
    return RuntimeError(f"{message} (host={host} port={port} auth={auth})")


_QUERY_PREFIXES = ("select", "with", "show", "describe")


def _prefix(sql: str) -> str:
    return sql.split(None, 1)[0].lower() if sql.split(None, 1) else ""


def _fetch(cursor) -> list[dict[str, Any]]:
    columns = [item[0] for item in cursor.description or []]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _safe_close(conn: Any) -> None:
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        return


def _using(work):
    captured = _guard_http_client()
    conn = None
    try:
        conn = _connect()
        try:
            return work(conn)
        finally:
            _safe_close(conn)
    except Exception as exc:
        raise _failure(exc, captured) from exc


def query_rows(sql: str) -> list[dict[str, Any]]:
    def work(conn):
        cursor = conn.cursor()
        cursor.execute(sql)
        return _fetch(cursor)

    return _using(work)


def execute_dml(sql: str) -> str:
    def work(conn):
        cursor = conn.cursor()
        cursor.execute(sql)
        return "ok"

    return _using(work)


def run_statements(statements: list[str]) -> list[dict[str, Any]]:
    """Run every statement on one Impala connection, in order."""

    def work(conn):
        cursor = conn.cursor()
        results: list[dict[str, Any]] = []
        for sql in statements:
            cursor.execute(sql)
            if _prefix(sql) in _QUERY_PREFIXES:
                results.append({"kind": "read", "rows": _fetch(cursor)})
            else:
                results.append({"kind": "write", "message": "ok"})
        return results

    return _using(work)
