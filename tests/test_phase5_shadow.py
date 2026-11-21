"""Phase 5: UI letter action and playbook parity with the legacy router."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from core.pack_manager import PackManager
from core import tools
from rules_engine.graph.entry_json import build_entry_graph
from rules_engine.graph.route_entry import route_entry
from rules_engine.pack import load_pack
from main import app

PACKS = REPO / "packs"
_PARITY = (
    "next_step",
    "agent_role",
    "allowed_tools",
    "write",
    "lane",
    "terminal",
    "letter_on_request",
    "routing_reason",
    "checks",
)


def _spine(**overrides: object) -> dict:
    row: dict = {
        "claim_id": 401,
        "claim_exists": True,
        "claim_number": "CLM-2025-000401",
        "claim_status_code": "OPEN",
        "litigation_indicator": False,
        "subrogation_indicator": False,
        "fraudulent_claim_indicator": False,
        "total_loss_indicator": False,
        "loss_event_id": 301,
        "loss_cause_code": "COLLISION",
        "policy_id": 1001,
        "policy_number": "PA-1001",
        "insurable_object_id": 201,
        "vin": "1HGBH41JXMN109186",
        "policy_covers_vehicle": True,
        "policy_coverage_id": 3001,
        "coverage_type_code": "COLLISION",
        "claim_lifecycle_id": 7001,
        "roles": [
            {"claim_party_role_id": 6001, "role_type_code": "INSURED", "party_id": 1},
            {"claim_party_role_id": 6002, "role_type_code": "ADJUSTER", "party_id": 4},
        ],
    }
    row.update(overrides)
    return row


def _operator(**overrides: object) -> dict:
    row: dict = {
        "was_cited_indicator": False,
        "impairment_suspected_indicator": False,
        "license_status_code": "VALID",
        "on_policy": True,
        "is_excluded_driver": False,
    }
    row.update(overrides)
    return row


def _discovery(**overrides: object) -> dict:
    row: dict = {
        "has_litigation_case": True,
        "litigation_case_id": 9101,
        "docket_number": "2025-CV-4412",
        "defense_counsel_party_id": 9,
        "plaintiff_counsel_party_id": 9,
        "served_date": "2025-08-05",
        "filed_date": "2025-08-01",
        "closed_date": None,
        "litigation_status_code": "IN_DISCOVERY",
        "has_police_report": True,
        "has_fault_determination": True,
    }
    row.update(overrides)
    return row


def _fnol_spine() -> dict:
    return {
        "fnol_id": 88,
        "fnol_number": "FNOL-2026-00088",
        "triage_status_code": "OPEN",
        "intake_purpose_code": "RECORD_ONLY_NOTIFICATION",
        "estimated_loss_amount": 450,
        "deductible_amount": 1000,
        "policy_id": 1088,
        "policy_number": "PA-9018442-01",
        "policy_status_code": "ACTIVE",
        "effective_date": "2026-01-01",
        "expiration_date": "2027-01-01",
        "cancellation_date": "",
        "loss_date": "2026-10-20",
        "insurable_object_id": 288,
        "vin": "4T1B11HK5NU123456",
        "vehicle_listed_on_policy": True,
        "peril_covered": True,
        "coverage_type_code": "COLLISION",
    }


def _fnol_signals() -> dict:
    return {
        "fnol_exists": True,
        "has_injury": False,
        "has_third_party_vehicle": False,
        "has_unlisted_insured_operator": False,
        "vehicle_listed_on_policy": True,
        "peril_covered": True,
        "estimated_loss_amount": 450,
        "deductible_amount": 1000,
        "intake_purpose_code": "RECORD_ONLY_NOTIFICATION",
        "triage_status_code": "OPEN",
    }


def _claims_case(seed: str, snapshot: str) -> dict:
    pack = load_pack(PACKS / "claims")
    if seed == "401" and snapshot == "police":
        return build_entry_graph(
            401,
            pack=pack,
            spine=_spine(),
            signals={
                "has_police_report": False,
                "has_incident_report_number": True,
                "incident_report_number": "SPD-25-11887",
                "has_fault_determination": True,
            },
        )
    if seed == "401" and snapshot == "incident":
        return build_entry_graph(
            401,
            pack=pack,
            spine=_spine(),
            signals={
                "has_police_report": False,
                "has_incident_report_number": False,
                "has_collect_incident_sms": False,
            },
        )
    if seed == "402":
        return build_entry_graph(
            402,
            pack=pack,
            spine=_spine(claim_id=402, litigation_indicator=True),
            signals=_discovery(),
        )
    if seed == "403":
        return build_entry_graph(
            403,
            pack=pack,
            spine=_spine(claim_id=403, claim_status_code="CLOSED"),
            signals={},
        )
    if seed == "404" and snapshot == "unlawful":
        return build_entry_graph(
            404,
            pack=pack,
            spine=_spine(claim_id=404),
            signals={"insured_operators": [_operator(impairment_suspected_indicator=True)]},
        )
    if seed == "404" and snapshot == "denied":
        return build_entry_graph(
            404,
            pack=pack,
            spine=_spine(claim_id=404, claim_status_code="DENIED"),
            signals={"insured_operators": [_operator(was_cited_indicator=True)]},
        )
    if seed == "88":
        return build_entry_graph(88, pack=pack, spine=_fnol_spine(), signals=_fnol_signals())
    raise AssertionError(f"no claims entry for {seed} {snapshot}")


def _mount_claims() -> PackManager:
    return PackManager("claims", str(PACKS))


def _evaluate(entity: str, case: dict) -> dict:
    from rules_engine.studio_io import graph_artifact_path, write_json_artifact

    path = graph_artifact_path(entity)
    write_json_artifact(path, case)
    tools._session_entity_id = entity
    tools._session_case_path = path
    return tools.evaluate_playbook()


def _distribution(monkeypatch, entity: str) -> tuple[dict, dict]:
    PackManager("retirement_distributions", str(PACKS))

    def fake_execute(op, label, params, manager):
        assert op == "query"
        fixture = PACKS / "retirement_distributions" / "fixtures" / label / f"{params['claim_id']}.json"
        return json.loads(fixture.read_text(encoding="utf-8"))

    monkeypatch.setattr(tools, "_call_mcp", fake_execute)
    tools.build_session_graph(entity)
    decision = tools.evaluate_playbook()
    case = json.loads(Path(tools._session_case_path).read_text(encoding="utf-8"))
    legacy = route_entry(
        case,
        entity,
        playbook_path=PackManager.active().playbook_path,
        pack_graph=dict(PackManager.active().manifest.get("graph") or {}),
    )
    return decision, legacy


@pytest.mark.parametrize(
    ("seed", "snapshot", "expected"),
    [
        ("401", "police", "RequestPoliceReport"),
        ("401", "incident", "CollectIncidentReportNumber"),
        ("402", "discovery", "EscalateDiscovery"),
        ("403", "closed", "CloseoutAudit"),
        ("404", "unlawful", "DenyUnlawfulOperation"),
        ("404", "denied", "DenyAudit"),
        ("88", "fnol", "ConvertFnolToIncidentOnly"),
        ("7001", "distribution", "ProcessDistribution"),
    ],
)
def test_playbook_matches_legacy_seed(seed, snapshot, expected, monkeypatch, tmp_path):
    monkeypatch.setenv("SESSION_DIRECTORY", str(tmp_path / "session"))
    if seed == "7001":
        decision, legacy = _distribution(monkeypatch, seed)
    else:
        _mount_claims()
        case = _claims_case(seed, snapshot)
        decision = _evaluate(seed, case)
        legacy = route_entry(
            case,
            seed,
            playbook_path=PackManager.active().playbook_path,
            pack_graph=dict(PackManager.active().manifest.get("graph") or {}),
        )
    assert decision["next_step"] == expected
    for key in _PARITY:
        assert decision[key] == legacy[key]


def test_action_writes_claim_letter(monkeypatch, tmp_path):
    monkeypatch.setenv("SESSION_DIRECTORY", str(tmp_path))
    response = TestClient(app).post(
        "/api/v1/action",
        json={
            "pack_id": "underwriting",
            "intent": "save_entry_artifact",
            "payload": {"entry_id": "401"},
        },
    )
    assert response.status_code == 200
    assert response.json() == {"status": "success", "artifact": "entry_401_letter.txt"}
    text = (tmp_path / "entry_401_letter.txt").read_text(encoding="utf-8")
    assert "401" in text
    assert not (tmp_path / "entry_401_sms.txt").exists()


def test_action_writes_sms_for_incident_step(monkeypatch, tmp_path):
    monkeypatch.setenv("SESSION_DIRECTORY", str(tmp_path))
    response = TestClient(app).post(
        "/api/v1/action",
        json={
            "pack_id": "underwriting",
            "intent": "save_entry_artifact",
            "payload": {
                "entry_id": "401",
                "artifact_type": "sms",
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["artifact"] == "entry_401_sms.txt"
    assert (tmp_path / "entry_401_sms.txt").is_file()
    assert not (tmp_path / "entry_401_letter.txt").exists()
