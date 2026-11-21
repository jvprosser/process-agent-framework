"""Coordinator URL parsing for the catalog Impala connection."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "mcp_forks" / "iceberg-mcp-server" / "src"))

from iceberg_mcp_server import impala


COORDINATOR = (
    "http://coordinator-default-impala-aws.dw-go01-demo-aws.ylcu-atmi.cloudera.site/"
)
HOST = "coordinator-default-impala-aws.dw-go01-demo-aws.ylcu-atmi.cloudera.site"


def _clear_impala(monkeypatch) -> None:
    for name in (
        "IMPALA_HOST",
        "IMPALA_PORT",
        "IMPALA_USER",
        "IMPALA_PASSWORD",
        "IMPALA_DATABASE",
        "IMPALA_AUTH_MECHANISM",
        "IMPALA_USE_SSL",
        "IMPALA_USE_HTTP_TRANSPORT",
        "IMPALA_HTTP_PATH",
        "IMPALA_JWT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_cdw_url_uses_the_original_ldap_settings(monkeypatch):
    _clear_impala(monkeypatch)
    monkeypatch.setenv("IMPALA_HOST", COORDINATOR)
    monkeypatch.setenv("IMPALA_PORT", "443")
    monkeypatch.setenv("IMPALA_USER", "lake-user")
    monkeypatch.setenv("IMPALA_PASSWORD", "lake-secret")
    monkeypatch.setenv("IMPALA_DATABASE", "car_insurance_claims")

    kwargs = impala.connection_kwargs()

    assert kwargs["host"] == HOST
    assert kwargs["port"] == 443
    assert kwargs["user"] == "lake-user"
    assert kwargs["password"] == "lake-secret"
    assert kwargs["database"] == "car_insurance_claims"
    assert kwargs["auth_mechanism"] == "LDAP"
    assert kwargs["use_ssl"] is True
    assert kwargs["use_http_transport"] is True
    assert kwargs["http_path"] == "cliservice"
    assert "jwt" not in kwargs


def test_unset_impala_env_matches_the_original_server(monkeypatch):
    _clear_impala(monkeypatch)
    monkeypatch.setattr(impala, "_JWT_PATH", Path("/no/such/jwt"))

    kwargs = impala.connection_kwargs()

    assert kwargs["host"] == "coordinator-default-impala.example.com"
    assert kwargs["port"] == 443
    assert kwargs["user"] == "username"
    assert kwargs["password"] == "password"
    assert kwargs["database"] == "default"
    assert kwargs["auth_mechanism"] == "LDAP"
    assert kwargs["use_http_transport"] is True
    assert kwargs["http_path"] == "cliservice"
    assert kwargs["use_ssl"] is True


def test_connection_failure_keeps_the_http_error(monkeypatch):
    _clear_impala(monkeypatch)
    monkeypatch.setenv("IMPALA_HOST", COORDINATOR)
    monkeypatch.setenv("IMPALA_PORT", "443")
    monkeypatch.setattr(impala, "_JWT_PATH", Path("/no/such/jwt"))
    error = impala._failure(
        AttributeError("'NoneType' object has no attribute 'close'"),
        {"detail": "HttpError: HTTP 401 Unauthorized body=Authentication failed"},
    )
    text = str(error)
    assert "HttpError: HTTP 401 Unauthorized" in text
    assert HOST in text
    assert "port=443" in text
    assert "auth=LDAP" in text
