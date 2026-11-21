"""Phase 20: CEL coercion comes from the active pack graph."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from rules_engine.graph.cel_rules import eval_cel, flatten_entry
from rules_engine.pack import load_pack

PACKS = REPO / "packs"


def test_each_pack_coerces_only_its_own_keys():
    claims = load_pack(PACKS / "claims").graph
    underwriting = load_pack(PACKS / "underwriting").graph
    distributions = load_pack(PACKS / "retirement_distributions").graph

    claim_row = flatten_entry({}, claims)
    assert claim_row["incident_report_number"] == ""
    assert claim_row["has_police_report"] is False
    assert claim_row["estimated_loss_amount"] == 0.0
    assert "tenant_roster_review_detail" not in claim_row
    assert "rmd_shortfall_amount" not in claim_row

    submission = flatten_entry({}, underwriting)
    assert submission["tenant_roster_review_detail"] == ""
    assert submission["total_insured_value"] == 0.0
    assert submission["inspection_completed"] is False
    assert "rmd_shortfall_amount" not in submission

    request = flatten_entry({}, distributions)
    assert request["rmd_shortfall_amount"] == 0.0
    assert request["hardship_reason_codes"] == []
    assert request["request_status_code"] == ""
    assert "tenant_roster_review_detail" not in request


def test_distribution_shortfall_uses_the_pack_number():
    graph = load_pack(PACKS / "retirement_distributions").graph
    assert eval_cel("rmd_shortfall_amount > 0", {"rmd_shortfall_amount": "4500"}, graph)
    assert eval_cel("rmd_shortfall_amount > 0", {}, graph) is False
