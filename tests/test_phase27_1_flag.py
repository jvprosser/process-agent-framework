"""Patch 27.1: fact assertions stay off unless FACT_ASSERTIONS_ENABLED is set."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from api import chat as chat_api
from core import tools
from core.pack_manager import PackManager
from core.tools import patch_session_field
from main import app

PACKS = REPO / "packs"


class _Message:
    def __init__(self, content=""):
        self.content = content
        self.tool_calls = None
        self.parsed = None


class _Response:
    def __init__(self, message):
        self.choices = [type("Choice", (), {"message": message})()]


class _Chat:
    def __init__(self):
        self.complete_calls = 0
        self.tools: list[list[str]] = []

    def parse(self, response_format, **kwargs):
        parsed = response_format(entity_id="401")
        return _Response(_Message(content=parsed.model_dump_json()))

    def complete(self, **kwargs):
        self.complete_calls += 1
        names = [tool["function"]["name"] for tool in kwargs.get("tools") or []]
        self.tools.append(names)
        return _Response(_Message(content="INTAKE_TEXT"))


class _Client:
    def __init__(self):
        self.chat = _Chat()


def _catalog(monkeypatch) -> None:
    def fake_execute(op, label, params, manager):
        if label == "get_claim_fact_assertions":
            raise AssertionError("session_assertions was read while facts are disabled")
        if label == "get_claim_spine":
            return {
                "ok": True,
                "spine": [
                    {
                        "claim_id": 401,
                        "claim_status_code": "OPEN",
                        "policy_id": 1001,
                        "insurable_object_id": 201,
                        "policy_covers_vehicle": True,
                        "has_adjuster": True,
                    }
                ],
            }
        if label == "get_claim_routing_signals":
            return {
                "ok": True,
                "signals": [
                    {
                        "has_police_report": False,
                        "incident_report_number": "",
                        "has_collect_incident_sms": False,
                    }
                ],
            }
        if op == "write":
            raise AssertionError(label)
        if op == "query":
            return {"ok": True}
        raise AssertionError(label)

    monkeypatch.setattr(tools, "_call_mcp", fake_execute)


def test_patch_is_rejected_and_does_not_write_the_sidecar(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_DIRECTORY", str(tmp_path))
    PackManager("claims", str(PACKS))
    tools._session_entity_id = "401"
    with pytest.raises(RuntimeError, match="Fact assertions are currently disabled"):
        patch_session_field("incident_report_number", "INC-2025-9999")
    assert not (tmp_path / "entry_401_overrides.json").exists()


def test_graph_ignores_overrides_and_still_derives_from_signals(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_DIRECTORY", str(tmp_path))
    PackManager("claims", str(PACKS)).activate()
    overrides = tmp_path / "entry_401_overrides.json"
    overrides.write_text(
        json.dumps(
            {
                "incident_report_number": {
                    "value": "INC-2025-9999",
                    "committed_to_lake": False,
                }
            }
        ),
        encoding="utf-8",
    )
    _catalog(monkeypatch)
    tools.build_session_graph("401")
    case = json.loads((tmp_path / "entry_401_case.json").read_text(encoding="utf-8"))
    assert case["incident_report_number"] == ""
    assert case["has_incident_report_number"] is False
    assert overrides.is_file()
    assert "INC-2025-9999" in overrides.read_text(encoding="utf-8")


def test_chat_hides_forms_answers_questions_and_blocks_facts(monkeypatch, tmp_path):
    monkeypatch.setenv("SESSION_DIRECTORY", str(tmp_path))
    _catalog(monkeypatch)
    client = _Client()
    monkeypatch.setattr(chat_api, "local_client", lambda: client)
    http = TestClient(app)
    asked = http.post(
        "/api/v1/chat",
        json={
            "user_text": "what is the incident report number?",
            "pack_id": "claims",
            "entity_id": "401",
            "lane": "PD",
        },
    )
    assert asked.status_code == 200
    body = asked.json()
    assert body["content"]["chat_text"] == "incident_report_number is not on file yet."
    assert body["actions"]["pending_forms"] == []
    assert "data_entry_panel" not in body["layout"]["widgets"]

    fact = http.post(
        "/api/v1/chat",
        json={
            "user_text": "the incident report number is INC-2025-9999",
            "pack_id": "claims",
            "entity_id": "401",
            "lane": "PD",
        },
    ).json()
    assert fact["meta"]["active_lane"] == "PD"
    assert fact["content"]["chat_text"] == "Direct fact overrides are currently disabled."
    assert fact["content"]["routing_summary"]["next_step"] == "CollectIncidentReportNumber"
    assert not (tmp_path / "entry_401_overrides.json").exists()
    assert client.chat.complete_calls == 2

    directive = http.post(
        "/api/v1/chat",
        json={
            "user_text": "write the police-report letter",
            "pack_id": "claims",
            "entity_id": "401",
            "lane": "PD",
        },
    )
    assert directive.status_code == 200
    specialist_tools = client.chat.tools[-1]
    assert "patch_session_field" not in specialist_tools
    assert "run_named_write" in specialist_tools


def test_submit_fact_form_is_forbidden_and_the_letter_action_remains(monkeypatch, tmp_path):
    monkeypatch.setenv("SESSION_DIRECTORY", str(tmp_path))
    http = TestClient(app)
    denied = http.post(
        "/api/v1/action",
        json={
            "pack_id": "claims",
            "intent": "submit_fact_form",
            "lane": "PD",
            "payload": {"entry_id": "401", "incident_report_number": "INC-2025-9999"},
        },
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "Fact assertions are currently disabled."
    assert not (tmp_path / "entry_401_overrides.json").exists()

    letter = http.post(
        "/api/v1/action",
        json={
            "pack_id": "claims",
            "intent": "save_entry_artifact",
            "payload": {"entry_id": "401"},
        },
    )
    assert letter.status_code == 200
    assert letter.json() == {"status": "success", "artifact": "entry_401_letter.txt"}
