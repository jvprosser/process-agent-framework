"""Phase 25: multi-lane directives and sidecar fact assertions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from api import chat as chat_api
from core import tools
from core.pack_manager import PackManager
from core.tools import flush_fact_assertions, patch_session_field
from main import app
from rules_engine.graph.route_entry import route_entry

PACKS = REPO / "packs"


def _route(pack_name: str, entry: dict, entity: str = "1") -> dict:
    pack = PackManager(pack_name, str(PACKS))
    return route_entry(
        entry,
        entity,
        playbook_path=pack.playbook_path,
        pack_graph=dict(pack.manifest.get("graph") or {}),
    )


def test_pack_load_rejects_an_action_missing_blocking(tmp_path: Path):
    playbook = {
        "priorities": ["R0"],
        "probes": {"R0": {"cel": "entry_exists == true"}},
        "actions": {"R0": [{"when": "ASK_TRUE", "step": "Wait", "agent": "Review", "lane": "REVIEW"}]},
        "default_action": {"step": "Wait", "agent": "Review", "lane": "REVIEW", "blocking": True},
    }
    path = tmp_path / "playbook.yaml"
    path.write_text(yaml.safe_dump(playbook), encoding="utf-8")
    with pytest.raises(RuntimeError, match="actions\\[R0\\]\\[0\\] is missing blocking"):
        route_entry({}, "1", playbook_path=path)


def test_claims_fnol_actions_stay_blocking_and_a_gate_replaces_earlier_lanes():
    playbook = yaml.safe_load((PACKS / "claims" / "playbook" / "playbook.yaml").read_text())
    for probe_id in ("F0", "F1", "F2", "F3", "F4", "F5", "F4b", "F6", "F7"):
        assert playbook["actions"][probe_id][0]["blocking"] is True

    closed = {
        "entry_exists": True,
        "claim_status_code": "CLOSED",
        "insured_operators": [{"was_cited_indicator": True}],
        "has_police_report": True,
        "has_fault_determination": True,
        "incident_report_number": "PR-1",
        "policy_id": 1,
        "insurable_object_id": 1,
        "policy_covers_vehicle": True,
        "has_adjuster": True,
    }
    decision = _route("claims", closed, "403")
    assert [item["directive_id"] for item in decision["routing_directives"]] == ["R5.2"]
    assert decision["routing_directives"][0]["blocking"] is True
    assert any(item["probe_id"] == "R1.1" and item["status"] == "assigned" for item in decision["checks"])


def test_each_lane_keeps_its_first_match_and_records_the_skip():
    entry = {
        "entry_exists": True,
        "claim_status_code": "OPEN",
        "policy_id": 1,
        "insurable_object_id": 1,
        "policy_covers_vehicle": True,
        "has_adjuster": True,
        "has_police_report": False,
        "has_collect_incident_sms": False,
        "incident_report_number": "",
        "has_fault_determination": False,
        "coverage_type_codes": ["COLLISION", "BI_LIABILITY"],
        "has_injury": False,
    }
    decision = _route("claims", entry, "401")
    lanes = [item["lane"] for item in decision["routing_directives"]]
    assert lanes.count("PD") == 1
    assert decision["routing_directives"][lanes.index("PD")]["next_step"] == "CollectIncidentReportNumber"
    assert "BI" in lanes
    skipped = [item for item in decision["checks"] if item.get("skipped_reason") == "lane_already_matched"]
    assert any(item["probe_id"] == "R2.1" for item in skipped)
    assigned = next(item for item in decision["checks"] if item["probe_id"] == "R2.0")
    assert assigned["status"] == "assigned"
    assert assigned["lane"] == "PD"
    assert next(item for item in skipped if item["probe_id"] == "R2.1")["lane"] == "PD"
    missed = next(item for item in decision["checks"] if item["probe_id"] == "R2.3")
    assert missed["status"] == "did_not_apply"
    assert missed["lane"] == "DATA_QUALITY"


def test_underwriting_exceptions_emit_one_review_directive():
    entry = {
        "entry_exists": True,
        "ocr_document_present": True,
        "min_ocr_confidence": 0.2,
        "naics_code": "325120",
    }
    decision = _route("underwriting", entry, "9100")
    assert len(decision["routing_directives"]) == 1
    review = decision["routing_directives"][0]
    assert review["directive_id"] == "exception_review"
    assert review["lane"] == "REVIEW"
    assert review["blocking"] is True
    assert review["next_step"] == "RESOLVE_DOCUMENT_EXCEPTIONS"
    assert all(item["lane"] != "DECLINE" for item in decision["routing_directives"])
    pack = PackManager("underwriting", str(PACKS))
    exception_ids = set(pack.playbook.get("exception_probes") or [])
    exception_checks = [item for item in decision["checks"] if item["probe_id"] in exception_ids]
    assert exception_checks
    assert all(item["lane"] == "REVIEW" for item in exception_checks)
    earlier = next(item for item in decision["checks"] if item["probe_id"] == "R0.1")
    assert earlier["status"] == "did_not_apply"
    assert earlier["lane"] == "DATA_QUALITY"


def test_retirement_packs_emit_directives_with_explicit_lanes():
    missing = {"entry_exists": False}
    distributions = _route("retirement_distributions", missing, "7001")
    rollovers = _route("retirement_rollovers", missing, "8002")
    assert distributions["routing_directives"][0]["lane"] == "DATA_QUALITY"
    assert distributions["routing_directives"][0]["blocking"] is True
    assert rollovers["routing_directives"][0]["lane"] == "DATA_QUALITY"
    assert rollovers["next_step"] == "FixDataQuality"


def test_patch_session_field_rejects_reserved_and_unknown_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_DIRECTORY", str(tmp_path))
    PackManager("claims", str(PACKS))
    tools._session_entity_id = "401"
    with pytest.raises(RuntimeError, match="not an ontology fact"):
        patch_session_field("next_step", "DetermineFault")
    with pytest.raises(RuntimeError, match="not an ontology fact"):
        patch_session_field("entry_exists", True)
    with pytest.raises(RuntimeError, match="not an ontology fact"):
        patch_session_field("not_a_field", "x")


def test_sidecar_evicts_when_the_lake_value_coerces_equal(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_DIRECTORY", str(tmp_path))
    PackManager("claims", str(PACKS)).activate()
    overrides = tmp_path / "entry_401_overrides.json"
    overrides.write_text(
        json.dumps(
            {
                "has_police_report": {
                    "value": True,
                    "asserted_by_role": "PdClaimsAgent",
                    "lane": "PD",
                    "http_run_id": "http-1",
                    "asserted_at": "2026-11-24T15:30:00Z",
                    "committed_to_lake": True,
                }
            }
        ),
        encoding="utf-8",
    )

    def fake_execute(op, label, params, manager):
        if label == "get_claim_fact_assertions":
            return {"ok": True, "assertions": [{"field_name": "has_police_report", "field_value": "true"}]}
        if label == "get_claim_spine":
            return {"ok": True, "spine": [{"claim_id": 401, "policy_id": 1, "has_police_report": False}]}
        if label == "get_claim_routing_signals":
            return {"ok": True, "signals": [{}]}
        raise AssertionError(label)

    monkeypatch.setattr(tools, "_call_mcp", fake_execute)
    tools.build_session_graph("401")
    assert not overrides.exists()
    case = json.loads((tmp_path / "entry_401_case.json").read_text(encoding="utf-8"))
    assert case["has_police_report"] in {True, "true"}


def test_missing_assertion_table_still_builds_the_session(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_DIRECTORY", str(tmp_path))
    PackManager("claims", str(PACKS)).activate()
    overrides = tmp_path / "entry_401_overrides.json"
    overrides.write_text(
        json.dumps(
            {
                "incident_report_number": {
                    "value": "PR-8841",
                    "asserted_by_role": "PdClaimsAgent",
                    "lane": "PD",
                    "http_run_id": "http-1",
                    "asserted_at": "2026-11-24T15:30:00Z",
                    "committed_to_lake": False,
                }
            }
        ),
        encoding="utf-8",
    )

    def fake_execute(op, label, params, manager):
        if label == "get_claim_fact_assertions":
            return {
                "ok": False,
                "error": (
                    "TrinoUserError(type=USER_ERROR, name=TABLE_NOT_FOUND, "
                    "message=\"Table 'iceberg.car_insurance_claims.claim_fact_assertions' does not exist\")"
                ),
            }
        if label == "get_claim_spine":
            return {"ok": True, "spine": [{"claim_id": 401, "policy_id": 1}]}
        if label == "get_claim_routing_signals":
            return {"ok": True, "signals": [{}]}
        raise AssertionError(label)

    monkeypatch.setattr(tools, "_call_mcp", fake_execute)
    tools.build_session_graph("401")
    case = json.loads((tmp_path / "entry_401_case.json").read_text(encoding="utf-8"))
    assert case["entry_exists"] is True
    assert case["incident_report_number"] == "PR-8841"
    assert overrides.is_file()


def test_assertion_read_failure_still_stops_the_graph(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_DIRECTORY", str(tmp_path))
    PackManager("claims", str(PACKS)).activate()

    def fake_execute(op, label, params, manager):
        if label == "get_claim_fact_assertions":
            return {"ok": False, "error": "coordinator unavailable"}
        if label == "get_claim_spine":
            return {"ok": True, "spine": [{"claim_id": 401}]}
        if label == "get_claim_routing_signals":
            return {"ok": True, "signals": [{}]}
        raise AssertionError(label)

    monkeypatch.setattr(tools, "_call_mcp", fake_execute)
    with pytest.raises(RuntimeError, match="coordinator unavailable"):
        tools.build_session_graph("401")


def test_flush_keeps_the_sidecar_when_the_insert_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_DIRECTORY", str(tmp_path))
    PackManager("claims", str(PACKS)).activate()
    tools._session_entity_id = "401"
    tools.set_assertion_context(role="PdClaimsAgent", lane="PD", http_run_id="http-1")
    patch_session_field("incident_report_number", "PR-8841")
    seen = {}

    def fake_execute(op, label, params, manager):
        seen["params"] = params
        return {"ok": False, "error": "down"}

    monkeypatch.setattr(tools, "_call_mcp", fake_execute)
    assert flush_fact_assertions("401") is False
    stored = json.loads((tmp_path / "entry_401_overrides.json").read_text(encoding="utf-8"))
    assert stored["incident_report_number"]["committed_to_lake"] is False
    assert seen["params"]["run_id"] == "run-401"
    assert seen["params"]["claim_id"] == "401"
    assert "incident_report_number" in seen["params"]["assertions_json"]
    sql = (PACKS / "underwriting" / "catalog" / "sql" / "trino" / "get_underwriting_fact_assertions.sql").read_text()
    assert "submission_id = ${var:claim_id}" in sql


class _Function:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _Call:
    def __init__(self, name, arguments):
        self.function = _Function(name, arguments)


class _Message:
    def __init__(self, content="", tool_calls=None, parsed=None):
        self.content = content
        self.tool_calls = tool_calls
        self.parsed = parsed


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
        return _Response(_Message(content=parsed.model_dump_json(), parsed=parsed))

    def complete(self, **kwargs):
        self.complete_calls += 1
        system = kwargs["messages"][0]["content"]
        if "patch_session_field" in system and "PdClaimsAgent" in system:
            call = _Call(
                "patch_session_field",
                json.dumps({"field": "incident_report_number", "value": "PR-8841"}),
            )
            return _Response(_Message(content="Recorded the incident report number.", tool_calls=[call]))
        return _Response(_Message(content="{}"))


class _Client:
    def __init__(self):
        self.chat = _Chat()


def _claims_catalog(monkeypatch, *, write_ok=True):
    def fake_execute(op, label, params, manager):
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
        if op == "write":
            return {"ok": write_ok, "named_op": label}
        if op == "query":
            return {"ok": True}
        raise AssertionError(label)

    monkeypatch.setattr(tools, "_call_mcp", fake_execute)


def test_turn_two_skips_the_orchestrator_and_advances_pd_once(monkeypatch, tmp_path):
    monkeypatch.setenv("SESSION_DIRECTORY", str(tmp_path))
    client = _Client()
    _claims_catalog(monkeypatch)
    monkeypatch.setattr(chat_api, "local_client", lambda: client)
    response = TestClient(app).post(
        "/api/v1/chat",
        json={
            "user_text": "The incident report number is PR-8841",
            "pack_id": "claims",
            "entity_id": "401",
            "lane": "PD",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert client.chat.parse_calls == 0
    assert client.chat.complete_calls == 1
    pd = next(item for item in body["content"]["routing_directives"] if item["lane"] == "PD")
    assert pd["next_step"] == "RequestPoliceReport"
    assert body["content"]["routing_summary"]["next_step"] == "RequestPoliceReport"
    assert body["meta"]["dialect"] == "trino"
    stored = json.loads((tmp_path / "entry_401_overrides.json").read_text(encoding="utf-8"))
    assert stored["incident_report_number"]["committed_to_lake"] is True


def test_a_blocking_gate_overrides_the_requested_lane(monkeypatch, tmp_path):
    monkeypatch.setenv("SESSION_DIRECTORY", str(tmp_path))
    client = _Client()

    def fake_execute(op, label, params, manager):
        if label == "get_claim_fact_assertions":
            return {"ok": True, "assertions": []}
        if label == "get_claim_spine":
            return {"ok": True, "spine": []}
        if label == "get_claim_routing_signals":
            return {"ok": True, "signals": []}
        if op == "write":
            return {"ok": True}
        if op == "query":
            return {"ok": True}
        raise AssertionError(label)

    monkeypatch.setattr(tools, "_call_mcp", fake_execute)
    monkeypatch.setattr(chat_api, "local_client", lambda: client)
    response = TestClient(app).post(
        "/api/v1/chat",
        json={"user_text": "status", "pack_id": "claims", "entity_id": "401", "lane": "PD"},
    )
    body = response.json()
    assert body["content"]["routing_directives"][0]["agent_role"] == "DataQualityAgent"
    assert body["content"]["routing_directives"][0]["blocking"] is True
    assert body["meta"]["active_lane"] == "DATA_QUALITY"
    assert client.chat.parse_calls == 0


def test_entity_id_path_traversal_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("SESSION_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(chat_api, "local_client", lambda: _Client())
    response = TestClient(app).post(
        "/api/v1/chat",
        json={"user_text": "hello", "pack_id": "claims", "entity_id": "../401", "lane": "PD"},
    )
    assert "not a safe session name" in response.json()["content"]["chat_text"]


def test_specialist_prompts_call_patch_session_field():
    for path in PACKS.glob("*/agents/*.md"):
        assert "patch_session_field" in path.read_text(encoding="utf-8")
