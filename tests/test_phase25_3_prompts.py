"""Patch 25.3: prompt purge, patch-turn write skip, and isolated reasoning."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from api import chat as chat_api
from core import tools
from main import app

PACKS = REPO / "packs"
_BANNED = ("coworker:", "demo-401-pd", "from Orchestrator")


class _Function:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _Call:
    def __init__(self, name, arguments):
        self.function = _Function(name, arguments)


class _Message:
    def __init__(self, content="", tool_calls=None, reasoning_content=""):
        self.content = content
        self.tool_calls = tool_calls
        self.parsed = None
        self.reasoning_content = reasoning_content


class _Response:
    def __init__(self, message):
        self.choices = [type("Choice", (), {"message": message})()]


class _Chat:
    def __init__(self, specialist):
        self.specialist = specialist
        self.parse_calls = 0

    def parse(self, response_format, **kwargs):
        self.parse_calls += 1
        parsed = response_format(entity_id="401")
        return _Response(_Message(content=parsed.model_dump_json(), parsed=parsed))

    def complete(self, **kwargs):
        system = kwargs["messages"][0]["content"]
        if "PdClaimsAgent" in system:
            return _Response(self.specialist)
        return _Response(_Message(content="{}"))


class _Client:
    def __init__(self, specialist):
        self.chat = _Chat(specialist)


def _claims_catalog(monkeypatch, writes: list[str]):
    def fake_execute(op, label, params, manager):
        if op == "write":
            writes.append(label)
            return {"ok": True, "named_op": label}
        if label == "get_claim_fact_assertions":
            return {"ok": True, "assertions": []}
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
                        "has_collect_incident_sms": False,
                        "incident_report_number": "",
                        "has_fault_determination": False,
                    }
                ],
            }
        if op == "query":
            return {"ok": True}
        raise AssertionError(label)

    monkeypatch.setattr(tools, "_call_mcp", fake_execute)


def _post(monkeypatch, tmp_path, specialist):
    monkeypatch.setenv("SESSION_DIRECTORY", str(tmp_path))
    writes: list[str] = []
    _claims_catalog(monkeypatch, writes)
    client = _Client(specialist)
    monkeypatch.setattr(chat_api, "local_client", lambda: client)
    response = TestClient(app).post(
        "/api/v1/chat",
        json={
            "user_text": "The police incident report number is INC-2025-9999",
            "pack_id": "claims",
            "entity_id": "401",
            "lane": "PD",
        },
    )
    assert response.status_code == 200
    return response.json(), writes


def test_agent_prompts_have_no_legacy_coworker_boilerplate():
    paths = list(PACKS.glob("*/agents/*.md"))
    assert paths
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for banned in _BANNED:
            assert banned not in text, f"{path.name} still contains {banned}"
        assert "patch_session_field" in text
        assert "view_data" in text


def test_think_blocks_stay_in_debug_cot(monkeypatch, tmp_path):
    visible = "Recorded incident report INC-2025-9999."
    body, _writes = _post(
        monkeypatch,
        tmp_path,
        _Message(content=f"<think>Call get_pd_view then create_pd_task.</think>\n{visible}"),
    )
    assert body["content"]["chat_text"] != visible
    assistant = [item["text"] for item in body["content"]["lane_messages"] if item["role"] == "assistant"]
    assert assistant == [body["content"]["chat_text"]]
    assert "<think>" not in body["content"]["chat_text"]
    assert all("<think>" not in item for item in assistant)
    cot = "\n".join(body["meta"]["debug_cot"])
    assert visible in cot
    assert "<think>" in cot


def test_structured_thinking_chunks_stay_in_debug_cot(monkeypatch, tmp_path):
    visible = "Recorded incident report INC-2025-9999."
    body, _writes = _post(
        monkeypatch,
        tmp_path,
        _Message(
            content=[
                {"type": "thinking", "thinking": "The stale step is CollectIncidentReportNumber."},
                {"type": "text", "text": visible},
            ],
            reasoning_content="sdk reasoning stays off the chat transcript",
        ),
    )
    assert visible not in body["content"]["chat_text"]
    assistant = [item["text"] for item in body["content"]["lane_messages"] if item["role"] == "assistant"]
    assert assistant == [body["content"]["chat_text"]]
    cot = "\n".join(body["meta"]["debug_cot"])
    assert visible in cot
    assert "CollectIncidentReportNumber" in cot
    assert "sdk reasoning stays off the chat transcript" in cot
    assert "sdk reasoning" not in body["content"]["chat_text"]
    assert "The stale step is" not in body["content"]["chat_text"]


def test_thought_tags_stay_in_debug_cot(monkeypatch, tmp_path):
    visible = "Recorded the incident report number."
    body, _writes = _post(
        monkeypatch,
        tmp_path,
        _Message(content=f"<thought>Do not call create_pd_task.</thought>{visible}"),
    )
    assert visible not in body["content"]["chat_text"]
    assert "<thought>" not in body["content"]["chat_text"]
    cot = "\n".join(body["meta"]["debug_cot"])
    assert visible in cot
    assert "<thought>" in cot


def test_patch_turn_skips_the_stale_write(monkeypatch, tmp_path):
    call = _Call(
        "patch_session_field",
        json.dumps({"field": "incident_report_number", "value": "INC-2025-9999"}),
    )
    body, writes = _post(
        monkeypatch,
        tmp_path,
        _Message(content="Recorded the incident report number.", tool_calls=[call]),
    )
    assert "create_pd_task" not in writes
    assert "Recorded the incident report number." in "\n".join(body["meta"]["debug_cot"])
    assert "incident_report_number INC-2025-9999" in body["content"]["chat_text"]
    assert "RequestPoliceReport" in body["content"]["chat_text"]
    stored = json.loads((tmp_path / "entry_401_overrides.json").read_text(encoding="utf-8"))
    assert stored["incident_report_number"]["value"] == "INC-2025-9999"
