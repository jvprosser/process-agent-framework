```json
{
  "probe_id": "R0.1",
  "title": "Claim exists in the case file",
  "status": "assigned",
  "detail": "Claim is missing from the case file"
}

```

The issue is clear from the JSON envelope. While Impala successfully returned the spine and routing signals (which are heavily populated with data like `claim_number: "CLM-2025-000401"` and `claim_status_code: "OPEN"`), the CEL rules engine still thinks the claim does not exist.

If you look closely at the bottom of the `session_graph` output, you will see this key-value pair:
`"claim_exists": null`

The `FixDataQuality` route is triggered by probe `R0.1`, which relies on `case.claim_exists == true`. The JSON graph builder (`case_json.py`) is failing to set this root-level property properly, despite having all the necessary data from the Impala queries.

Here is the Phase 15 hotfix assignment for Grok to correct the graph materialization and unblock the claims routing.

---

### Phase 15: Session Graph Property Resolution

**To: Grok (Cursor)**
**From: Implementation Architect**

The data pipeline from Impala is now successfully pulling the spine and routing signals into the `session_graph` payload. However, the `rules_engine` JSON builder is failing to compute the root-level boolean flags derived from that data (such as `claim_exists`), leaving them as `null`. Because `claim_exists` is null, the CEL playbook (Probe `R0.1`) incorrectly routes to `FixDataQuality`.

#### Task 1: Fix Derived Boolean Logic in Graph Builder

* Open `src/rules_engine/graph/case_json.py` (or the file responsible for assembling the JSON graph from the Impala read payloads).
* Locate the logic that populates the root-level fields (likely mapping the raw `spine` data into the flattened `case` properties).
* Add explicit logic to evaluate the existence of the core entity.
* For example:
```python
# If spine data was returned, the claim exists.
case_data["claim_exists"] = bool(case_data.get("claim_id"))

# Or, if this is an FNOL:
if "fnol_id" in case_data:
    case_data["fnol_exists"] = bool(case_data.get("fnol_id"))

```




* Ensure that any other required root-level properties (like `has_adjuster` or `eval_date`) are safely derived from the `spine` and `signals` dictionaries if they are missing or `null` at the root.

**Phase 15 Acceptance Criteria:**

1. Submitting a prompt for Claim 401 returns the JSON envelope with `content.session_graph.claim_exists` evaluating to `true`.
2. The `content.checks` array correctly skips `R0.1` and evaluates the remainder of the probes.
3. `content.routing_summary.next_step` accurately evaluates to `RequestPoliceReport` (or another valid operational step) rather than `FixDataQuality`.