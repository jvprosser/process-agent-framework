Grok is absolutely right. Those are the deep, hidden tendrils of a monolithic architecture—the kind of silent defaults that work perfectly until you deploy a new domain and spend three days debugging why a commercial rollover is checking for a "triangle" of vehicles.

Here is the Phase 21 assignment to eradicate these final behavioral remnants and truly generalize the engine.

---

### Phase 21: Deep Domain Purge & Generic Artifacts

**To: Grok (Cursor)**
**From: Implementation Architect**

The rules of engagement remain in force. While the type lists are gone, the runtime still silently enforces claims-specific behavior in the entry builder, the payload shaper, the artifact tool, and the MCP SQL binder.

In this phase, you will remove these hidden domain rules so the engine executes exactly what the active pack dictates—nothing more, nothing less.

#### Task 1: Purge Claims Logic from the Entry Builder

* Open `src/rules_engine/graph/entry_json.py`.
* Locate `_fill_missing_entry_flags` (or its equivalent).
* Delete the hardcoded logic that derives `has_adjuster`, `has_policy`, `has_vehicle`, `policy_covers_vehicle`, and `triangle`. If a pack requires these flags, it must return them natively in its SQL spine query or evaluate them directly in its CEL playbook.

#### Task 2: Generalize Spine & Signal Shaping

* Open `src/rules_engine/studio_io.py`.
* In `normalize_signals_payload`, remove the hardcoded lifting of specific claims keys (`injury_ids`, `offers`, `insured_operators`, `payment_ids`, `recovery_ids`, `document_ids`). The function should just return the `signals` object as provided by the MCP JSON.
* In `normalize_spine_payload`, remove the hardcoded fallbacks that copy `roles`, `claim_id`, and `fnol_id` into the spine dictionary.
* Open `src/core/tools.py`. In `_as_graph_object`, remove the claims-specific hack that explicitly copies `roles` onto the spine row.

#### Task 3: Domain-Agnostic Artifact Tool

* Rename `save_claim_letter` to `save_entry_artifact` across the codebase (including `src/core/tools.py`, `src/rules_engine/studio_io.py`, `src/api/chat.py`, and `src/api/action.py`).
* In `studio_io.py`, remove the hardcoded default steps (`LitigationSupport` and `CollectIncidentReportNumber`). Update the function to accept a generic `artifact_type` (e.g., `letter`, `sms`, `notice`) and write the file as `entry_{id}_{artifact_type}.txt`.
* In `tools.py`, update the `@tool` decorator description and parameter names to reflect the generic `entry_id` and `artifact_type`.

#### Task 4: Clean the MCP SQL Binder

* Open `mcp_forks/iceberg-mcp-server/src/iceberg_mcp_server/catalog.py`.
* In `_bind_params`, remove the hardcoded `_ALIAS_KEYS` completely. Stop copying `claim_id` to `submission_id` or `case_id`. The application will pass exactly the parameters defined in the pack's `catalog.yaml`.
* Remove the `_parse_fnol_id` function that strips the `FNOL-` prefix. ID coercion should either apply generically to strings/integers or be handled by the pack's SQL/rules.

#### Task 5: Clean Environment Variables

* In `src/rules_engine/paths.py` and `studio_io.py`, remove all references to `INS_CLAIMS_REPO_ROOT`. Use `PACK_ROOT` exclusively to resolve the active domain.

**Phase 21 Acceptance Criteria:**

1. The entry builder, payload normalizer, and SQL binder do not contain any hardcoded references to `claims`, `adjuster`, `triangle`, `fnol`, `submission`, or `roles`.
2. The `save_entry_artifact` tool successfully writes generic `entry_{id}_{type}.txt` files to the session directory without checking for legacy claims steps.
3. The test suite passes, verifying that all packs (Claims, Finserv, Underwriting) still function end-to-end relying entirely on their own SQL and CEL definitions.