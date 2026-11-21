"""Phase 3: /api/v1/chat returns the four-node envelope."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from api import chat as chat_api
from main import app

PACKS = REPO / "packs"
PROMPT_MARK = "You record a playbook decline after route_claim assigns"
SPECIALIST_TEXT = "Recorded the playbook write and left the letter unsent."


class _Message:
    def __init__(self, content="", parsed=None):
        self.content = content
        self.tool_calls = None
        self.parsed = parsed


class _Response:
    def __init__(self, message):
        self.choices = [type("Choice", (), {"message": message})()]


class _Chat:
    def parse(self, response_format, **kwargs):
        user = kwargs["messages"][-1]["content"]
        entity_id = "9100" if user == "Please process submission 9100" else None
        parsed = response_format(entity_id=entity_id)
        return _Response(_Message(content=parsed.model_dump_json(), parsed=parsed))

    def complete(self, **kwargs):
        system = kwargs["messages"][0]["content"]
        if PROMPT_MARK in system:
            return _Response(_Message(content=SPECIALIST_TEXT))
        return _Response(_Message(content='{"next_step": "BIND"}'))


class _Client:
    chat = _Chat()


def _install_fakes(monkeypatch):
    def fake_execute(op, label, params, manager):
        assert manager.catalog_path == PACKS / "underwriting" / "catalog" / "catalog.yaml"
        if op == "write":
            return {"ok": True, "named_op": label, "named_op_kind": "write"}
        fixture = PACKS / "underwriting" / "fixtures" / label / f"{params['claim_id']}.json"
        return json.loads(fixture.read_text(encoding="utf-8"))

    import core.tools as tools

    monkeypatch.setattr(tools, "_call_mcp", fake_execute)
    monkeypatch.setattr(chat_api, "local_client", lambda: _Client())


def test_chat_envelope_routes_submission(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SESSION_DIRECTORY", str(tmp_path))
    _install_fakes(monkeypatch)
    client = TestClient(app)

    response = client.post(
        "/api/v1/chat",
        json={
            "user_text": "Please process submission 9100",
            "pack_id": "underwriting",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert set(body) == {"meta", "layout", "content", "actions"}
    uuid.UUID(body["meta"]["run_id"])
    assert body["meta"]["pack_id"] == "underwriting"
    assert body["meta"]["entity_id"] == "9100"
    assert body["meta"]["terminal"] is True
    assert any(line.startswith("evaluate_playbook ") for line in body["meta"]["debug_cot"])
    assert "run_named_write write_submission_decline" in body["meta"]["debug_cot"]
    assert body["layout"]["widgets"] == ["chat_feed", "routing_summary_panel"]
    assert body["layout"] == {
        "template": "split_pane",
        "widgets": ["chat_feed", "routing_summary_panel"],
        "focus": "chat_feed",
    }
    assert body["content"]["chat_text"] == SPECIALIST_TEXT
    assert body["content"]["routing_summary"]["next_step"] == "DECLINE_UNINSURABLE_CLASS"
    assert body["content"]["routing_summary"]["agent_role"] == "UnderwritingIntakeSpecialist"
    assert body["content"]["routing_summary"]["lane"] == "DECLINE"
    assert body["content"]["routing_summary"]["routing_reason"]
    assert body["content"]["checks"][-1]["probe_id"] == "P00_PROHIBITED_CLASS_CODE"
    assert body["content"]["view_data"] == {}
    case_path = tmp_path / "entry_9100_case.json"
    assert body["content"]["session_graph"] == json.loads(case_path.read_text(encoding="utf-8"))
    assert body["actions"] == {
        "quick_replies": ["Please write the recommended letter."],
        "tool_invocations": [
            {
                "label": "Draft Recommended Letter",
                "intent": "save_claim_letter",
                "payload": {"entry_id": "9100"},
            }
        ],
    }


def test_chat_without_an_id_skips_routing(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SESSION_DIRECTORY", str(tmp_path))
    _install_fakes(monkeypatch)
    client = TestClient(app)

    response = client.post(
        "/api/v1/chat",
        json={"user_text": "What should we do next?", "pack_id": "underwriting"},
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"meta", "layout", "content", "actions"}
    assert body["meta"]["entity_id"] is None
    assert body["meta"]["debug_cot"] == ["orchestrator: no entity id"]
    assert body["meta"]["terminal"] is False
    assert "couldn't find an ID" in body["content"]["chat_text"]
    assert body["content"]["routing_summary"]["next_step"] is None
    assert body["content"]["checks"] == []
    assert body["content"]["session_graph"] == {}
