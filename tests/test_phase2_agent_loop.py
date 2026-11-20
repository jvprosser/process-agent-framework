"""Phase 2: orchestrator extraction, intake routing, and specialist write."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from core.agent_loop import run_intake, run_orchestrator, run_specialist
from core.pack_manager import PackManager

PACKS = REPO / "packs"
PROMPT_MARK = "You record a playbook decline after route_claim assigns"


class _Message:
    def __init__(self, content="", tool_calls=None, parsed=None):
        self.content = content
        self.tool_calls = tool_calls
        self.parsed = parsed


class _Response:
    def __init__(self, message):
        self.choices = [type("Choice", (), {"message": message})()]


class _Chat:
    def __init__(self, specialist_text: str):
        self.specialist_text = specialist_text
        self.parse_calls = []
        self.complete_calls = []

    def parse(self, response_format, **kwargs):
        self.parse_calls.append({"response_format": response_format, **kwargs})
        user = kwargs["messages"][-1]["content"]
        entity_id = "9100" if user == "Please process submission 9100" else None
        parsed = response_format(entity_id=entity_id)
        return _Response(_Message(content=parsed.model_dump_json(), parsed=parsed))

    def complete(self, **kwargs):
        self.complete_calls.append(kwargs)
        messages = kwargs["messages"]
        system = messages[0]["content"]
        if PROMPT_MARK in system:
            return _Response(_Message(content=self.specialist_text))
        return _Response(_Message(content='{"next_step": "BIND"}'))


class _Client:
    def __init__(self, specialist_text: str):
        self.chat = _Chat(specialist_text)


def _catalog(monkeypatch):
    def fake_execute(*, op, label, params, catalog_file, **_kwargs):
        assert catalog_file == PACKS / "underwriting" / "catalog" / "catalog.yaml"
        if op == "write":
            assert label == "write_submission_decline"
            assert params["run_id"]
            assert "entity_id" in json.loads(params["event_json"])
            return {"ok": True, "named_op": label, "named_op_kind": "write"}
        fixture = PACKS / "underwriting" / "fixtures" / label / f"{params['claim_id']}.json"
        return json.loads(fixture.read_text(encoding="utf-8"))

    import rules_engine.named_catalog as named_catalog

    monkeypatch.setattr(named_catalog, "execute", fake_execute)


def test_orchestrator_extracts_entity_id_and_returns_none_without_one():
    client = _Client("unused")
    assert run_orchestrator("Please process submission 9100", client) == "9100"
    assert run_orchestrator("What should we do next?", client) is None
    sent = client.chat.parse_calls[0]
    assert sent["response_format"].__name__ == "EntityExtraction"
    assert sent["messages"][1]["content"] == "Please process submission 9100"


def test_intake_returns_playbook_route_not_the_model_text(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SESSION_DIRECTORY", str(tmp_path))
    _catalog(monkeypatch)
    pack = PackManager("underwriting", str(PACKS))
    client = _Client("unused")

    route = run_intake("9100", client, pack)

    assert route["next_step"] == "DECLINE_UNINSURABLE_CLASS"
    assert route["agent_role"] == "UnderwritingIntakeSpecialist"
    assert route["write"] == "write_submission_decline"
    assert route["allowed_tools"] == [
        "write_submission_decline",
        "save_claim_letter",
    ]
    offered = client.chat.complete_calls[0]["tools"]
    assert [tool["function"]["name"] for tool in offered] == [
        "build_session_graph",
        "evaluate_playbook",
    ]


def test_specialist_loads_role_prompt_and_executes_the_write(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SESSION_DIRECTORY", str(tmp_path))
    _catalog(monkeypatch)
    pack = PackManager("underwriting", str(PACKS))
    client = _Client("Recorded the playbook write and left the letter unsent.")
    route = run_intake("9100", client, pack)

    result = run_specialist(route, client, pack)

    assert result["text"] == "Recorded the playbook write and left the letter unsent."
    assert result["route_json"] is route
    assert route["view_data"] == {}
    assert route["letter_on_request"] is True
    assert result["tool_invocations"] == [
        {
            "tool": "run_named_write",
            "label": "write_submission_decline",
            "result": {
                "ok": True,
                "named_op": "write_submission_decline",
                "named_op_kind": "write",
            },
        }
    ]
    specialist_call = client.chat.complete_calls[-1]
    assert PROMPT_MARK in specialist_call["messages"][0]["content"]
    assert [tool["function"]["name"] for tool in specialist_call["tools"]] == [
        "run_named_write",
    ]
