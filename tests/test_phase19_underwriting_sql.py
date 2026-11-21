"""Phase 19: underwriting labels render as SQL and keep the submission id numeric."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "mcp_forks" / "iceberg-mcp-server" / "src"))

from iceberg_mcp_server.catalog import execute

PACK = REPO / "packs" / "underwriting"


def test_submission_spine_uses_an_unquoted_submission_id():
    seen: list[str] = []

    def query_rows(sql: str) -> list[dict]:
        seen.append(sql)
        return [{"submission_id": 9100, "class_code": "4812", "naics_code": "325120"}]

    result = execute(
        "query",
        "get_submission_spine",
        {"claim_id": "9100"},
        root=PACK,
        query_rows=query_rows,
    )

    assert result["ok"] is True
    assert result["spine"][0]["class_code"] == "4812"
    assert "submission_id = 9100" in seen[0]
    assert "submission_id = '9100'" not in seen[0]
    assert "handler" not in seen[0]


def test_catalog_has_no_handler_labels():
    text = (PACK / "catalog" / "catalog.yaml").read_text(encoding="utf-8")
    assert "handler:" not in text
