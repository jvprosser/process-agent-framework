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


def test_cdw_url_uses_the_hostname_and_the_workbench_jwt(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("IMPALA_HOST", COORDINATOR)
    monkeypatch.setenv("IMPALA_PORT", "443")
    monkeypatch.delenv("IMPALA_AUTH_MECHANISM", raising=False)
    monkeypatch.delenv("IMPALA_USER", raising=False)
    monkeypatch.delenv("IMPALA_PASSWORD", raising=False)
    monkeypatch.delenv("IMPALA_JWT", raising=False)
    monkeypatch.delenv("IMPALA_USE_SSL", raising=False)
    monkeypatch.delenv("IMPALA_USE_HTTP_TRANSPORT", raising=False)
    jwt = tmp_path / "jwt"
    jwt.write_text('{"access_token": "workload-jwt"}', encoding="utf-8")
    monkeypatch.setattr(impala, "_JWT_PATH", jwt)

    kwargs = impala.connection_kwargs()

    assert kwargs["host"] == (
        "coordinator-default-impala-aws.dw-go01-demo-aws.ylcu-atmi.cloudera.site"
    )
    assert kwargs["port"] == 443
    assert kwargs["use_ssl"] is True
    assert kwargs["use_http_transport"] is True
    assert kwargs["http_path"] == "cliservice"
    assert kwargs["auth_mechanism"] == "JWT"
    assert kwargs["jwt"] == "workload-jwt"
    assert "user" not in kwargs
    assert "database" not in kwargs


def test_local_impala_stays_on_nosasl(monkeypatch):
    monkeypatch.setenv("IMPALA_HOST", "localhost")
    monkeypatch.delenv("IMPALA_PORT", raising=False)
    monkeypatch.delenv("IMPALA_AUTH_MECHANISM", raising=False)
    monkeypatch.delenv("IMPALA_JWT", raising=False)
    monkeypatch.setattr(impala, "_JWT_PATH", Path("/no/such/jwt"))

    kwargs = impala.connection_kwargs()

    assert kwargs["host"] == "localhost"
    assert kwargs["port"] == 21050
    assert kwargs["auth_mechanism"] == "NOSASL"
    assert kwargs["use_http_transport"] is False
    assert kwargs["use_ssl"] is False


def test_connection_failure_keeps_the_http_error(monkeypatch):
    monkeypatch.setenv("IMPALA_HOST", COORDINATOR)
    monkeypatch.setenv("IMPALA_PORT", "443")
    monkeypatch.delenv("IMPALA_AUTH_MECHANISM", raising=False)
    monkeypatch.setattr(impala, "_JWT_PATH", Path("/no/such/jwt"))
    error = impala._failure(
        AttributeError("'NoneType' object has no attribute 'close'"),
        {"detail": "HttpError: HTTP 401 Unauthorized body=Authentication failed"},
    )
    text = str(error)
    assert "HttpError: HTTP 401 Unauthorized" in text
    assert "coordinator-default-impala-aws.dw-go01-demo-aws.ylcu-atmi.cloudera.site" in text
    assert "port=443" in text
