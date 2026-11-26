"""Phase 26: schema-driven forms, tile layout, and action re-evaluation."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from core.agent_loop import build_layout, synthesize_lane_response
from core.pack_manager import PackManager
from core import tools
from main import app
from rules_engine.graph.route_entry import validate_playbook_forms

PACKS = REPO / "packs"


def _claims() -> PackManager:
    return PackManager("claims", str(PACKS))


def test_validation_reads_action_forms_and_ignores_probe_ask():
    pack = _claims()
    playbook = pack.playbook
    assert playbook["probes"]["R2.0"]["form"] == "ASK"
    validate_playbook_forms(
        playbook,
        set(pack.ontology_field_names()),
        dict(pack.manifest.get("graph") or {}),
    )


def test_validation_fails_closed():
    pack = _claims()
    graph = dict(pack.manifest.get("graph") or {})
    fields = set(pack.ontology_field_names())
    missing = copy.deepcopy(pack.playbook)
    missing["actions"]["R2.0"][0]["form"] = "not_a_form"
    with pytest.raises(RuntimeError, match="not defined"):
        validate_playbook_forms(missing, fields, graph)

    reserved = copy.deepcopy(pack.playbook)
    reserved["forms"]["collect_incident_report"]["fields"][0]["id"] = "next_step"
    with pytest.raises(RuntimeError, match="not an ontology fact"):
        validate_playbook_forms(reserved, fields, graph)

    mismatched = copy.deepcopy(pack.playbook)
    mismatched["forms"]["confirm_police_report_status"]["fields"][0]["type"] = "text"
    with pytest.raises(RuntimeError, match="disagrees with graph"):
        validate_playbook_forms(mismatched, fields, graph)

    select = copy.deepcopy(pack.playbook)
    select["forms"]["collect_incident_report"]["fields"][0]["type"] = "select"
    select["forms"]["collect_incident_report"]["fields"][0]["options"] = []
    with pytest.raises(RuntimeError, match="missing options"):
        validate_playbook_forms(select, fields, graph)


def test_build_layout_switches_on_forms_and_lane_count():
    split = build_layout([{"lane": "PD"}], [])
    assert split["template"] == "split_pane"
    assert split["widgets"] == ["chat_feed", "routing_summary_panel", "rule_probes"]
    assert split["focus"] == "chat_feed"

    tiled = build_layout(
        [{"lane": "PD"}, {"lane": "BI"}],
        [],
    )
    assert tiled["template"] == "tile_grid"
    assert "routing_summary_panel" in tiled["widgets"]
    assert "data_entry_panel" not in tiled["widgets"]
    assert tiled["focus"] == "chat_feed"

    formed = build_layout([{"lane": "PD"}], [{"lane": "PD", "title": "Incident report number"}])
    assert formed["template"] == "tile_grid"
    assert "data_entry_panel" in formed["widgets"]
    assert "routing_summary_panel" in formed["widgets"]
    assert formed["focus"] == "data_entry_panel"


def test_synthesis_names_the_stored_value_and_stays_on_the_speaking_lane():
    playbook = _claims().playbook
    text = synthesize_lane_response(
        [
            {
                "lane": "DATA_QUALITY",
                "next_step": "FixDataQuality",
                "agent_role": "DataQualityAgent",
                "routing_reason": "The claim is missing.",
                "blocking": True,
            }
        ],
        [{"probe_id": "R0.1", "title": "Claim on file", "status": "assigned", "lane": "DATA_QUALITY"}],
        [],
        "PD",
        playbook,
        {"incident_report_number": "INC-2025-9999"},
    )
    assert "incident_report_number INC-2025-9999" in text
    assert "PD is paused because blocking gate DATA_QUALITY" in text
    assert "Claim on file (assigned)" in text


def _catalog(monkeypatch):
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
            return {"ok": True, "named_op": label}
        if op == "query":
            return {"ok": True}
        raise AssertionError(label)

    monkeypatch.setattr(tools, "_call_mcp", fake_execute)


def test_submit_fact_form_advances_pd_and_accepts_false(monkeypatch, tmp_path):
    monkeypatch.setenv("SESSION_DIRECTORY", str(tmp_path))
    _catalog(monkeypatch)
    client = TestClient(app)
    missing_lane = client.post(
        "/api/v1/action",
        json={
            "pack_id": "claims",
            "intent": "submit_fact_form",
            "payload": {"entry_id": "401", "incident_report_number": "INC-2025-9999"},
        },
    )
    assert missing_lane.status_code == 400

    unknown = client.post(
        "/api/v1/action",
        json={
            "pack_id": "claims",
            "intent": "submit_fact_form",
            "lane": "PD",
            "payload": {"entry_id": "401", "not_a_field": "x", "incident_report_number": "INC-2025-9999"},
        },
    )
    assert unknown.status_code == 400

    advanced = client.post(
        "/api/v1/action",
        json={
            "pack_id": "claims",
            "intent": "submit_fact_form",
            "lane": "PD",
            "payload": {
                "entry_id": "401",
                "form_id": "collect_incident_report",
                "incident_report_number": "INC-2025-9999",
            },
        },
    )
    assert advanced.status_code == 200
    body = advanced.json()
    assert body["status"] == "success"
    assert body["chat_response"]["meta"]["active_lane"] == "PD"
    assert "incident_report_number INC-2025-9999" in body["chat_response"]["content"]["chat_text"]
    summary = body["chat_response"]["content"]["routing_summary"]
    assert summary["next_step"] == "RequestPoliceReport"
    form_ids = [item["form_id"] for item in body["chat_response"]["actions"]["pending_forms"]]
    assert form_ids == ["confirm_police_report_status"]
    transcript = json.loads((tmp_path / "entry_401_chat_PD.json").read_text(encoding="utf-8"))
    assert transcript[-1]["role"] == "assistant"
    assert transcript[-1]["lane"] == "PD"

    denied = client.post(
        "/api/v1/action",
        json={
            "pack_id": "claims",
            "intent": "submit_fact_form",
            "lane": "PD",
            "payload": {"entry_id": "401", "form_id": "confirm_police_report_status", "has_police_report": False},
        },
    )
    assert denied.status_code == 200
    assert denied.json()["chat_response"]["content"]["routing_summary"]["next_step"] == "RequestPoliceReport"
    assert "has_police_report false" in denied.json()["chat_response"]["content"]["chat_text"]

    confirmed = client.post(
        "/api/v1/action",
        json={
            "pack_id": "claims",
            "intent": "submit_fact_form",
            "lane": "PD",
            "payload": {"entry_id": "401", "form_id": "confirm_police_report_status", "has_police_report": True},
        },
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "success"
    assert confirmed.json()["chat_response"]["content"]["routing_summary"]["next_step"] == "DetermineFault"


def test_save_entry_artifact_omits_chat_response(monkeypatch, tmp_path):
    monkeypatch.setenv("SESSION_DIRECTORY", str(tmp_path))
    response = TestClient(app).post(
        "/api/v1/action",
        json={
            "pack_id": "claims",
            "intent": "save_entry_artifact",
            "payload": {"entry_id": "401"},
        },
    )
    assert response.status_code == 200
    assert response.json() == {"status": "success", "artifact": "entry_401_letter.txt"}
