"""Phase 27: deterministic fact, question, and directive turns."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from api import chat as chat_api
from core import tools
from core.pack_manager import PackManager
from core.turn_classifier import classify_turn, coerce_fact_value
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
        self.parse_calls = 0
        self.complete_calls = 0

    def parse(self, response_format, **kwargs):
        self.parse_calls += 1
        parsed = response_format(entity_id="401")
        return _Response(_Message(content=parsed.model_dump_json()))

    def complete(self, **kwargs):
        self.complete_calls += 1
        system = kwargs["messages"][0]["content"]
        text = "INTAKE_TEXT" if "build_session_graph" in system else "SPECIALIST_TEXT"
        return _Response(_Message(content=text))


class _Client:
    def __init__(self):
        self.chat = _Chat()


def _open_claim(monkeypatch, writes: list[str], *, spine: dict | None = None, signals: dict | None = None):
    row = spine if spine is not None else {
        "claim_id": 401,
        "claim_status_code": "OPEN",
        "policy_id": 1001,
        "insurable_object_id": 201,
        "policy_covers_vehicle": True,
        "has_adjuster": True,
    }
    signal = signals if signals is not None else {
        "has_police_report": False,
        "has_collect_incident_sms": False,
        "incident_report_number": "",
        "has_fault_determination": False,
    }

    def fake_execute(op, label, params, manager):
        if op == "write":
            writes.append(label)
            return {"ok": True, "named_op": label}
        if label == "get_claim_fact_assertions":
            return {"ok": True, "assertions": []}
        if label == "get_claim_spine":
            return {"ok": True, "spine": [row] if row else []}
        if label == "get_claim_routing_signals":
            return {"ok": True, "signals": [signal]}
        if op == "query":
            return {"ok": True}
        raise AssertionError(label)

    monkeypatch.setattr(tools, "_call_mcp", fake_execute)


def _post(monkeypatch, tmp_path, payload: dict):
    monkeypatch.setenv("SESSION_DIRECTORY", str(tmp_path))
    writes: list[str] = []
    spine = payload.pop("_spine", None)
    signals = payload.pop("_signals", None)
    _open_claim(monkeypatch, writes, spine=spine, signals=signals)
    client = _Client()
    monkeypatch.setattr(chat_api, "local_client", lambda: client)
    response = TestClient(app).post("/api/v1/chat", json=payload)
    assert response.status_code == 200
    return response.json(), writes, client


def test_classifier_uses_the_longest_field_phrase_and_ignores_comments():
    pack = PackManager("claims", str(PACKS))
    fact = classify_turn("the incident report number is INC-2025-9999", pack)
    assert fact.kind == "fact"
    assert fact.field_id == "incident_report_number"
    assert fact.value == "INC-2025-9999"

    derived = classify_turn("has incident report number is true", pack)
    assert derived.kind == "fact"
    assert derived.field_id == "has_incident_report_number"
    assert derived.value == "true"

    question = classify_turn("what is the incident report number?", pack)
    assert question.kind == "question"
    assert question.field_id == "incident_report_number"

    derived_question = classify_turn("what is has incident report number?", pack)
    assert derived_question.kind == "question"
    assert derived_question.field_id == "has_incident_report_number"

    assert classify_turn("write the police-report letter", pack).kind == "directive"
    assert classify_turn("status", pack).kind == "directive"
    both = "the incident report number is INC-1 and has police report is true"
    assert classify_turn(both, pack).kind == "directive"


def test_strict_coercion_rejects_silent_fallbacks():
    pack = PackManager("claims", str(PACKS))
    assert coerce_fact_value("has_police_report", "yes", pack) is True
    assert coerce_fact_value("has_police_report", "0", pack) is False
    assert coerce_fact_value("estimated_loss_amount", "0", pack) == 0
    assert coerce_fact_value("coverage_type_codes", '["PD", "BI"]', pack) == ["PD", "BI"]
    for field, span in (
        ("has_police_report", "maybe"),
        ("estimated_loss_amount", "twelve"),
        ("coverage_type_codes", "PD, BI"),
    ):
        try:
            coerce_fact_value(field, span, pack)
        except ValueError:
            continue
        raise AssertionError(f"{field} accepted {span}")


def test_fact_turn_records_the_number_and_recomputes_the_derived_flag(monkeypatch, tmp_path):
    monkeypatch.setenv("FACT_ASSERTIONS_ENABLED", "true")
    body, writes, client = _post(
        monkeypatch,
        tmp_path,
        {
            "user_text": "the incident report number is INC-2025-9999",
            "pack_id": "claims",
            "entity_id": "401",
            "lane": "PD",
        },
    )
    assert client.chat.complete_calls == 1
    assert "create_pd_task" not in writes
    assert "INTAKE_TEXT" in "\n".join(body["meta"]["debug_cot"])
    assert "SPECIALIST_TEXT" not in "\n".join(body["meta"]["debug_cot"])
    assert "build_session_graph" in "\n".join(body["meta"]["debug_cot"])
    assert body["content"]["view_data"] == {}
    assert body["meta"]["active_lane"] == "PD"
    assert "incident_report_number INC-2025-9999" in body["content"]["chat_text"]
    assert body["content"]["routing_summary"]["next_step"] == "RequestPoliceReport"
    assert body["content"]["session_graph"]["incident_report_number"] == "INC-2025-9999"
    assert body["content"]["session_graph"]["has_incident_report_number"] is True
    transcript = json.loads((tmp_path / "entry_401_chat_PD.json").read_text(encoding="utf-8"))
    assert [item["role"] for item in transcript] == ["user", "assistant"]
    assert transcript[-1]["text"] == body["content"]["chat_text"]
    assert transcript[-1]["agent_role"] == "PdClaimsAgent"
    assert body["content"]["lane_messages"][-1]["text"] == body["content"]["chat_text"]


def test_fact_on_a_derived_field_is_rejected_without_a_write(monkeypatch, tmp_path):
    monkeypatch.setenv("FACT_ASSERTIONS_ENABLED", "true")
    body, writes, client = _post(
        monkeypatch,
        tmp_path,
        {
            "user_text": "has incident report number is true",
            "pack_id": "claims",
            "entity_id": "401",
            "lane": "PD",
        },
    )
    assert client.chat.complete_calls == 1
    assert writes == []
    assert body["meta"]["active_lane"] == "PD"
    assert body["content"]["chat_text"] == (
        "has_incident_report_number is a derived field and cannot be set directly."
    )
    assert body["content"]["routing_summary"]["next_step"] == "CollectIncidentReportNumber"
    assert not (tmp_path / "entry_401_overrides.json").exists()
    transcript = json.loads((tmp_path / "entry_401_chat_PD.json").read_text(encoding="utf-8"))
    assert transcript[-1]["text"] == body["content"]["chat_text"]


def test_questions_read_stored_derived_zero_false_and_missing_values(monkeypatch, tmp_path):
    monkeypatch.setenv("FACT_ASSERTIONS_ENABLED", "true")
    monkeypatch.setenv("SESSION_DIRECTORY", str(tmp_path))
    writes: list[str] = []
    _open_claim(monkeypatch, writes)
    client = _Client()
    monkeypatch.setattr(chat_api, "local_client", lambda: client)
    http = TestClient(app)

    missing = http.post(
        "/api/v1/chat",
        json={
            "user_text": "what is the incident report number?",
            "pack_id": "claims",
            "entity_id": "401",
            "lane": "PD",
        },
    ).json()
    assert missing["content"]["chat_text"] == "incident_report_number is not on file yet."
    assert missing["content"]["routing_summary"]["next_step"] == "CollectIncidentReportNumber"
    assert not (tmp_path / "entry_401_overrides.json").exists()

    http.post(
        "/api/v1/chat",
        json={
            "user_text": "the incident report number is INC-2025-9999",
            "pack_id": "claims",
            "entity_id": "401",
            "lane": "PD",
        },
    )
    stored = http.post(
        "/api/v1/chat",
        json={
            "user_text": "what is the incident report number?",
            "pack_id": "claims",
            "entity_id": "401",
            "lane": "PD",
        },
    ).json()
    assert stored["content"]["chat_text"] == "incident_report_number is INC-2025-9999."
    derived = http.post(
        "/api/v1/chat",
        json={
            "user_text": "what is has incident report number?",
            "pack_id": "claims",
            "entity_id": "401",
            "lane": "PD",
        },
    ).json()
    assert derived["content"]["chat_text"] == "has_incident_report_number is True."

    (tmp_path / "entry_401_overrides.json").write_text(
        json.dumps(
            {
                "incident_report_number": {
                    "value": "",
                    "asserted_by_role": "UserLaneChat",
                    "lane": "PD",
                    "http_run_id": "earlier",
                    "asserted_at": "2026-11-27T12:00:00Z",
                    "committed_to_lake": True,
                },
                "estimated_loss_amount": {
                    "value": 0,
                    "asserted_by_role": "UserLaneChat",
                    "lane": "PD",
                    "http_run_id": "earlier",
                    "asserted_at": "2026-11-27T12:00:00Z",
                    "committed_to_lake": True,
                },
            }
        ),
        encoding="utf-8",
    )
    cleared = http.post(
        "/api/v1/chat",
        json={
            "user_text": "what is has incident report number?",
            "pack_id": "claims",
            "entity_id": "401",
            "lane": "PD",
        },
    ).json()
    assert cleared["content"]["chat_text"] == "has_incident_report_number is False."
    zero = http.post(
        "/api/v1/chat",
        json={
            "user_text": "what is estimated loss amount?",
            "pack_id": "claims",
            "entity_id": "401",
            "lane": "PD",
        },
    ).json()
    assert zero["content"]["chat_text"] == "estimated_loss_amount is 0."
    flag = http.post(
        "/api/v1/chat",
        json={
            "user_text": "what is has police report?",
            "pack_id": "claims",
            "entity_id": "401",
            "lane": "PD",
        },
    ).json()
    assert flag["content"]["chat_text"] == "has_police_report is False."
    assert "INTAKE_TEXT" in "\n".join(flag["meta"]["debug_cot"])
    assert "SPECIALIST_TEXT" not in "\n".join(flag["meta"]["debug_cot"])


def test_fact_and_question_stay_on_pd_while_a_gate_is_up(monkeypatch, tmp_path):
    monkeypatch.setenv("FACT_ASSERTIONS_ENABLED", "true")
    body, _writes, client = _post(
        monkeypatch,
        tmp_path,
        {
            "user_text": "the incident report number is INC-2025-9999",
            "pack_id": "claims",
            "entity_id": "401",
            "lane": "PD",
            "_spine": {},
            "_signals": {"incident_report_number": "", "has_incident_report_number": False},
        },
    )
    assert client.chat.complete_calls == 1
    assert body["meta"]["active_lane"] == "PD"
    assert body["content"]["routing_directives"][0]["lane"] == "DATA_QUALITY"
    assert body["content"]["routing_directives"][0]["blocking"] is True
    assert len(body["content"]["routing_directives"]) == 1
    assert body["content"]["routing_summary"]["lane"] == "DATA_QUALITY"
    transcript = json.loads((tmp_path / "entry_401_chat_PD.json").read_text(encoding="utf-8"))
    assert transcript[-1]["agent_role"] is None
    assert transcript[-1]["lane"] == "PD"

    asked, _writes, _client = _post(
        monkeypatch,
        tmp_path,
        {
            "user_text": "what is the incident report number?",
            "pack_id": "claims",
            "entity_id": "401",
            "lane": "PD",
            "_spine": {},
            "_signals": {"incident_report_number": ""},
        },
    )
    assert asked["meta"]["active_lane"] == "PD"
    assert asked["content"]["routing_summary"]["lane"] == "DATA_QUALITY"
    assert asked["content"]["lane_messages"][-1]["agent_role"] is None

    missing, _writes, _client = _post(
        monkeypatch,
        tmp_path,
        {
            "user_text": "what is the incident report number?",
            "pack_id": "claims",
            "entity_id": "401",
            "lane": "NOPE",
            "_spine": {},
            "_signals": {},
        },
    )
    assert missing["content"]["chat_text"] == "Lane NOPE has no active directive."
    assert missing["meta"]["active_lane"] is None
    assert not (tmp_path / "entry_401_chat_NOPE.json").exists()


def test_directives_still_reach_the_specialist(monkeypatch, tmp_path):
    for text in (
        "write the police-report letter",
        "hello there",
        "the incident report number is INC-1 and has police report is true",
    ):
        body, writes, client = _post(
            monkeypatch,
            tmp_path,
            {"user_text": text, "pack_id": "claims", "entity_id": "401", "lane": "PD"},
        )
        assert client.chat.complete_calls == 2, text
        assert "SPECIALIST_TEXT" in "\n".join(body["meta"]["debug_cot"])
        assert "create_pd_task" in writes


def test_coercion_failures_stay_on_the_lane_without_a_write(monkeypatch, tmp_path):
    monkeypatch.setenv("FACT_ASSERTIONS_ENABLED", "true")
    samples = (
        ("has police report is maybe", "has_police_report"),
        ("estimated loss amount is twelve", "estimated_loss_amount"),
        ("coverage type codes is PD, BI", "coverage_type_codes"),
    )
    for text, field_id in samples:
        body, writes, _client = _post(
            monkeypatch,
            tmp_path,
            {"user_text": text, "pack_id": "claims", "entity_id": "401", "lane": "PD"},
        )
        assert writes == [], text
        assert body["meta"]["active_lane"] == "PD"
        assert body["content"]["routing_summary"]["next_step"] == "CollectIncidentReportNumber"
        assert body["content"]["chat_text"] == (
            f"The value for {field_id} could not be coerced and was not recorded."
        )
        assert not (tmp_path / "entry_401_overrides.json").exists()


def test_inactive_lane_and_omitted_lane(monkeypatch, tmp_path):
    inactive, _writes, client = _post(
        monkeypatch,
        tmp_path,
        {
            "user_text": "what is the incident report number?",
            "pack_id": "claims",
            "entity_id": "401",
            "lane": "BI",
        },
    )
    assert client.chat.complete_calls == 1
    assert inactive["content"]["chat_text"] == "Lane BI has no active directive."
    assert inactive["meta"]["active_lane"] is None
    assert not (tmp_path / "entry_401_chat_BI.json").exists()

    omitted, _writes, client = _post(
        monkeypatch,
        tmp_path,
        {
            "user_text": "the incident report number is INC-2025-9999",
            "pack_id": "claims",
            "entity_id": "401",
        },
    )
    assert client.chat.complete_calls == 2
    assert "SPECIALIST_TEXT" in "\n".join(omitted["meta"]["debug_cot"])
