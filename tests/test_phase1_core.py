"""Phase 1: pack mount, local Mistral client, and deterministic tool wrappers."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from core.mistral_client import LOCAL_API_KEY, local_client
from core.pack_manager import PackManager
from core import tools

PACKS = REPO / "packs"


def test_pack_manager_mounts_underwriting_playbook_and_catalog():
    manager = PackManager("underwriting", str(PACKS))
    assert manager.playbook["version"] == "2.10.0"
    assert "get_submission_spine" in manager.catalog["reads"]
    assert "write_submission_decline" in manager.catalog["writes"]
    assert "class_code" in manager.ontology_field_names()
    assert PackManager.active() is manager


def test_missing_pack_and_missing_folder_raise(tmp_path: Path):
    with pytest.raises(RuntimeError, match="not found"):
        PackManager("does_not_exist", str(PACKS))

    broken = tmp_path / "packs" / "empty_pack"
    (broken / "ontology").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="missing required folders"):
        PackManager("empty_pack", str(tmp_path / "packs"))


def test_local_client_uses_dummy_key_and_local_server(monkeypatch):
    monkeypatch.delenv("MISTRAL_LOCAL_URL", raising=False)
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.delenv("CDP_TOKEN", raising=False)
    from core import mistral_client

    monkeypatch.setattr(mistral_client, "_JWT_PATH", Path("/no/such/jwt"))
    client = local_client()
    url, _params = client.sdk_configuration.get_server_details()
    assert url == "http://localhost:8000"
    assert "api.mistral.ai" not in url
    security = client.sdk_configuration.security
    api_key = security.api_key
    if hasattr(api_key, "get_secret_value"):
        api_key = api_key.get_secret_value()
    assert api_key == LOCAL_API_KEY


def test_tools_are_native_and_route_from_the_playbook(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SESSION_DIRECTORY", str(tmp_path))
    PackManager("underwriting", str(PACKS))
    missing = tools.run_named_query("not_a_label", {"claim_id": "9100"})
    assert missing["ok"] is False
    assert missing["label"] == "not_a_label"
    assert [tool.name for tool in tools.CORE_TOOLS] == [
        "run_named_query",
        "run_named_write",
        "build_session_graph",
        "evaluate_playbook",
    ]

    def fake_execute(op, label, params, manager):
        assert manager.catalog_path == PACKS / "underwriting" / "catalog" / "catalog.yaml"
        if op == "write":
            return {"ok": True, "named_op": label, "named_op_kind": "write"}
        fixture = PACKS / "underwriting" / "fixtures" / label / f"{params['claim_id']}.json"
        return json.loads(fixture.read_text(encoding="utf-8"))

    monkeypatch.setattr(tools, "_call_mcp", fake_execute)

    built = tools.build_session_graph("9100")
    case_path = Path(built["session_case"])
    assert case_path.is_file()
    assert case_path.name == "claim_9100_case.json"
    case = json.loads(case_path.read_text(encoding="utf-8"))
    assert case["class_code"] == "4812"

    decision = tools.evaluate_playbook()
    assert decision["next_step"] == "DECLINE_UNINSURABLE_CLASS"
    assert decision["agent_role"] == "UnderwritingIntakeSpecialist"
    assert decision["allowed_tools"] == [
        "write_submission_decline",
        "save_claim_letter",
    ]

    queried = tools.run_named_query("get_submission_spine", {"claim_id": "9100"})
    assert queried["spine"]["class_code"] == "4812"
    written = tools.run_named_write("write_submission_decline", {"claim_id": "9100"})
    assert written["ok"] is True
    assert written["named_op"] == "write_submission_decline"
