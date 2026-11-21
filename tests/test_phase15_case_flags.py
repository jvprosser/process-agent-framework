"""Phase 15: a populated spine row sets claim_exists so R0.1 can pass."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from rules_engine.graph.case_json import build_pack_case
from rules_engine.pack import load_pack

PACKS = REPO / "packs"


def test_spine_row_sets_claim_exists_and_triangle():
    pack = load_pack(PACKS / "claims")
    case = build_pack_case(
        "401",
        pack=pack,
        spine={
            "claim_id": 401,
            "claim_number": "CLM-2025-000401",
            "claim_status_code": "OPEN",
            "policy_id": 9,
            "insurable_object_id": 3,
            "policy_covers_vehicle": True,
            "roles": [{"role_type_code": "ADJUSTER"}],
        },
        signals={"has_police_report": False},
    )

    assert case["claim_exists"] is True
    assert case["fnol_exists"] is False
    assert case["triangle"] is True
    assert case["has_adjuster"] is True
    assert case["eval_date"]


def test_empty_spine_does_not_invent_a_claim():
    pack = load_pack(PACKS / "claims")
    case = build_pack_case("401", pack=pack, spine={}, signals={})

    assert case["claim_id"] == "401"
    assert case["claim_exists"] is False
    assert case["triangle"] is False
