"""Patch 26.2: derived ontology fields are blocked on write and recomputed on the graph."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from core import tools
from core.pack_manager import PackManager
from core.tools import patch_session_field
from main import app

PACKS = REPO / "packs"


def _copy_claims(tmp_path: Path) -> Path:
    root = tmp_path / "packs"
    shutil.copytree(PACKS / "claims", root / "claims")
    return root


def _set_derived(root: Path, derived: dict[str, str]) -> None:
    path = root / "claims" / "pack.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["derived"] = derived
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def test_claims_loads_derived_and_other_packs_do_not():
    claims = PackManager("claims", str(PACKS))
    assert claims.derived == {
        "has_incident_report_number": 'incident_report_number != ""'
    }
    for name in ("underwriting", "retirement_distributions", "retirement_rollovers"):
        assert PackManager(name, str(PACKS)).derived == {}


def test_pack_load_rejects_a_derived_field_outside_the_ontology(tmp_path):
    root = _copy_claims(tmp_path)
    _set_derived(root, {"not_a_field": 'incident_report_number != ""'})
    with pytest.raises(RuntimeError, match="not an ontology fact"):
        PackManager("claims", str(root))


def test_pack_load_rejects_a_reserved_derived_field(tmp_path):
    root = _copy_claims(tmp_path)
    _set_derived(root, {"entry_exists": "true"})
    with pytest.raises(RuntimeError, match="is reserved"):
        PackManager("claims", str(root))


def test_pack_load_rejects_invalid_derived_cel(tmp_path):
    root = _copy_claims(tmp_path)
    _set_derived(root, {"has_incident_report_number": "incident_report_number !="})
    with pytest.raises(RuntimeError, match="invalid CEL"):
        PackManager("claims", str(root))


def test_pack_load_rejects_a_form_that_collects_a_derived_field(tmp_path):
    root = _copy_claims(tmp_path)
    playbook_path = root / "claims" / "playbook" / "playbook.yaml"
    playbook = yaml.safe_load(playbook_path.read_text(encoding="utf-8"))
    playbook["forms"]["collect_incident_report"]["fields"][0]["id"] = "has_incident_report_number"
    playbook_path.write_text(yaml.safe_dump(playbook, sort_keys=False), encoding="utf-8")
    with pytest.raises(RuntimeError, match="is a derived field"):
        PackManager("claims", str(root))


def test_patch_rejects_a_derived_field_without_writing_the_sidecar(tmp_path, monkeypatch):
    monkeypatch.setenv("FACT_ASSERTIONS_ENABLED", "true")
    monkeypatch.setenv("SESSION_DIRECTORY", str(tmp_path))
    PackManager("claims", str(PACKS))
    tools._session_entity_id = "401"
    with pytest.raises(RuntimeError, match="is a derived field and cannot be set directly"):
        patch_session_field("has_incident_report_number", True)
    assert not (tmp_path / "entry_401_overrides.json").exists()


def _catalog(monkeypatch, signals: dict) -> None:
    def fake_execute(op, label, params, manager):
        if label == "get_claim_fact_assertions":
            return {"ok": True, "assertions": []}
        if label == "get_claim_spine":
            return {"ok": True, "spine": [{"claim_id": 401, "policy_id": 1}]}
        if label == "get_claim_routing_signals":
            return {"ok": True, "signals": [signals]}
        if op == "write":
            return {"ok": True}
        if op == "query":
            return {"ok": True}
        raise AssertionError(label)

    monkeypatch.setattr(tools, "_call_mcp", fake_execute)


def test_graph_recomputes_has_incident_report_number(tmp_path, monkeypatch):
    monkeypatch.setenv("FACT_ASSERTIONS_ENABLED", "true")
    monkeypatch.setenv("SESSION_DIRECTORY", str(tmp_path))
    PackManager("claims", str(PACKS)).activate()
    overrides = tmp_path / "entry_401_overrides.json"
    overrides.write_text(
        json.dumps(
            {
                "incident_report_number": {
                    "value": "INC-2025-9999",
                    "asserted_by_role": "PdClaimsAgent",
                    "lane": "PD",
                    "http_run_id": "http-1",
                    "asserted_at": "2026-11-27T12:00:00Z",
                    "committed_to_lake": False,
                }
            }
        ),
        encoding="utf-8",
    )
    _catalog(
        monkeypatch,
        {"has_incident_report_number": False, "incident_report_number": ""},
    )
    tools.build_session_graph("401")
    case = json.loads((tmp_path / "entry_401_case.json").read_text(encoding="utf-8"))
    assert case["incident_report_number"] == "INC-2025-9999"
    assert case["has_incident_report_number"] is True
    stored = json.loads(overrides.read_text(encoding="utf-8"))
    assert "has_incident_report_number" not in stored

    overrides.unlink()
    _catalog(
        monkeypatch,
        {"has_incident_report_number": True, "incident_report_number": ""},
    )
    tools.build_session_graph("401")
    case = json.loads((tmp_path / "entry_401_case.json").read_text(encoding="utf-8"))
    assert case["incident_report_number"] == ""
    assert case["has_incident_report_number"] is False


def test_submit_fact_form_rejects_a_derived_field(monkeypatch, tmp_path):
    monkeypatch.setenv("FACT_ASSERTIONS_ENABLED", "true")
    monkeypatch.setenv("SESSION_DIRECTORY", str(tmp_path))
    _catalog(monkeypatch, {"incident_report_number": "", "has_incident_report_number": False})
    response = TestClient(app).post(
        "/api/v1/action",
        json={
            "pack_id": "claims",
            "intent": "submit_fact_form",
            "lane": "PD",
            "payload": {
                "entry_id": "401",
                "form_id": "collect_incident_report",
                "incident_report_number": "INC-2025-9999",
                "has_incident_report_number": True,
            },
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == (
        "has_incident_report_number is a derived field and cannot be set directly."
    )
    assert not (tmp_path / "entry_401_overrides.json").exists()
