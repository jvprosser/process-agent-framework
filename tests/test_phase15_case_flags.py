"""Phase 15/16: a populated spine row sets entry_exists."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from rules_engine.graph.entry_json import build_entry_graph
from rules_engine.pack import load_pack

PACKS = REPO / "packs"


def test_spine_row_sets_entry_exists_and_triangle():
    pack = load_pack(PACKS / "claims")
    entry = build_entry_graph(
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

    assert entry["entry_id"] == "401"
    assert entry["entry_exists"] is True
    assert "triangle" not in entry
    assert "has_adjuster" not in entry
    assert entry["eval_date"]
    assert "claim_exists" not in entry
    assert "fnol_exists" not in entry


def test_empty_spine_does_not_invent_an_entry():
    pack = load_pack(PACKS / "claims")
    entry = build_entry_graph("401", pack=pack, spine={}, signals={})

    assert entry["entry_id"] == "401"
    assert entry["entry_exists"] is False
    assert "triangle" not in entry
