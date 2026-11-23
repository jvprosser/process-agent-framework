"""Phase 23: the same catalog label renders Impala or Trino SQL and returns the same rows."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "mcp_forks" / "iceberg-mcp-server" / "src"))

from iceberg_mcp_server.catalog import execute

PACK = REPO / "packs" / "underwriting"
ROW = {"submission_id": 9100, "class_code": "4812", "naics_code": "325120"}


def _run(dialect: str | None) -> tuple[dict, list[str]]:
    seen: list[str] = []

    def query_rows(sql: str) -> list[dict]:
        seen.append(sql)
        return [dict(ROW)]

    kwargs = {"dialect": dialect} if dialect else {}
    result = execute(
        "query",
        "get_submission_spine",
        {"claim_id": "9100"},
        root=PACK,
        query_rows=query_rows,
        **kwargs,
    )
    return result, seen


def test_both_dialects_return_the_same_spine_payload():
    impala, impala_sql = _run("impala")
    trino, trino_sql = _run("trino")

    assert impala["ok"] is True and trino["ok"] is True
    assert impala["spine"] == trino["spine"]
    assert impala["spine"][0]["naics_code"] == "325120"
    assert "submission_id = 9100" in impala_sql[0]
    assert "submission_id = 9100" in trino_sql[0]


def test_omitted_dialect_stays_on_impala():
    seen: list[str] = []
    result = execute(
        "query",
        "get_submission_routing_signals",
        {"claim_id": "9100"},
        root=PACK,
        query_rows=lambda sql: seen.append(sql) or [{"naics_code": "325120"}],
    )
    assert result["ok"] is True
    assert "unix_timestamp" in seen[0]
    assert "to_unixtime" not in seen[0]


def test_trino_signals_drop_impala_functions():
    seen: list[str] = []

    def query_rows(sql: str) -> list[dict]:
        seen.append(sql)
        return [{"naics_code": "325120"}]

    result = execute(
        "query",
        "get_submission_routing_signals",
        {"claim_id": "9100", "dialect": "trino"},
        root=PACK,
        query_rows=query_rows,
    )
    assert result["ok"] is True
    sql = seen[0]
    assert "to_unixtime" in sql
    assert "array_join" in sql
    assert "strpos(" in sql
    for leftover in ("unix_timestamp", "GROUP_CONCAT", "INSTR(", "AS STRING", "CURRENT_TIMESTAMP()"):
        assert leftover not in sql

    impala = execute(
        "query",
        "get_submission_routing_signals",
        {"claim_id": "9100", "dialect": "impala"},
        root=PACK,
        query_rows=query_rows,
    )
    assert impala["signals"] == result["signals"]
    assert "unix_timestamp" in seen[1]


def test_trino_schema_comes_from_the_pack_catalog(monkeypatch):
    monkeypatch.setenv("TRINO_SCHEMA", "ignored")
    seen: dict[str, str | None] = {}

    def run_statements(statements, dialect="impala", schema=None):
        seen["dialect"] = dialect
        seen["schema"] = schema
        return [{"kind": "read", "rows": [dict(ROW)]}]

    monkeypatch.setattr("iceberg_mcp_server.db.run_statements", run_statements)
    underwriting = execute(
        "query",
        "get_submission_spine",
        {"claim_id": "9100"},
        root=PACK,
        dialect="trino",
    )
    assert underwriting["ok"] is True
    assert seen == {"dialect": "trino", "schema": "underwriting"}

    claims = execute(
        "query",
        "get_claim_spine",
        {"claim_id": "401"},
        root=REPO / "packs" / "claims",
        dialect="trino",
    )
    assert claims["ok"] is True
    assert seen["schema"] == "car_insurance_claims"

    from iceberg_mcp_server.trino_client import connection_kwargs

    assert connection_kwargs("car_insurance_claims")["schema"] == "car_insurance_claims"


def test_trino_always_uses_https(monkeypatch):
    monkeypatch.setenv("TRINO_PORT", "8080")
    monkeypatch.setenv("TRINO_HTTP_SCHEME", "http")
    monkeypatch.setenv("TRINO_PASSWORD", "secret")

    from iceberg_mcp_server.trino_client import connection_kwargs

    kwargs = connection_kwargs("car_insurance_claims")
    assert kwargs["http_scheme"] == "https"
    assert kwargs["auth"].__class__.__name__ == "BasicAuthentication"


def test_unknown_dialect_is_rejected():
    result = execute(
        "query",
        "get_submission_spine",
        {"claim_id": "9100", "dialect": "postgres"},
        root=PACK,
        query_rows=lambda sql: [],
    )
    assert result["ok"] is False
    assert "invalid dialect" in result["error"]
