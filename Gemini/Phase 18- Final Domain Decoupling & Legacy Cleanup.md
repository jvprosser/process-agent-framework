### Phase 18: Final Domain Decoupling & Legacy Cleanup

**To: Grok (Cursor)**
**From: Implementation Architect**

The rules of engagement remain in force. In previous phases, we successfully moved all domain logic out of the backend rules engine into data-driven `packs/` and transitioned to a unified `iceberg-mcp-server` that executes SQL natively from the pack catalog.

However, several core files still contain hardcoded legacy domain terminology, obsolete Python query handlers, and fallback logic tailored specifically to the old `claims` architecture. In this phase, you will purge these remnants to finalize the domain-agnostic abstraction.

#### Task 1: Eradicate Legacy Catalog Handlers (`named_catalog.py`)

Because the unified `iceberg-mcp-server` now handles all named operations by executing SQL files defined in the pack's `catalog.yaml`, the Python-based handler routing is entirely obsolete.

* Open `src/rules_engine/named_catalog.py` (or `named_catalog_2.py`).
* Delete the hardcoded dictionary mappings: `PACKAGE_HANDLERS`, `_AUDIT_HANDLERS`, `HANDLER_NAMES`, `FINSERV_HANDLERS`, and `UNDERWRITING_HANDLERS`.
* Delete all the associated loader functions: `_handlers_for`, `_default_handlers`, `_audit_handlers`, `_finserv_handlers`, and `_underwriting_handlers`.
* Delete the `_run_handler` function. If the module is still used to route fixtures (`_run_fixture`), keep only the fixture logic and fail fast if a label asks for a `handler`. (If the entire file is no longer imported because `tools.py` routes directly to the MCP subprocess, you may delete the file entirely).

#### Task 2: Sanitize Pack Configuration (`pack.py`)

* Open `src/rules_engine/pack.py`.
* Rename the dataclass field `case_id_param` to `entry_id_param`. Update `load_pack` to parse `"entry_id_param"` with a fallback of `"entry_id"` instead of `"claim_id"`.
* Update the default schema path from `"ontology/claims.json"` to `"ontology/schema.json"`.
* Delete the `is_legacy_claims_root` function entirely.

#### Task 3: Clean Up Paths & I/O Assertions (`paths.py` & `studio_io.py`)

* Open `src/rules_engine/paths.py`. Remove any imports or calls to `is_legacy_claims_root`.
* Open `src/rules_engine/studio_io.py`.
* Remove calls to `is_legacy_claims_root` inside `configure_workflow_assets`.
* Delete the hardcoded domain validation functions. Remove `assert_spine_has_triangle_fields`, `assert_signals_has_fnol_fields`, and `assert_signals_has_intake_fields`. The rules engine will no longer manually validate these specific claims/FNOL attributes; that responsibility now rests natively in the pack's CEL playbook.
* Update `graph_artifact_path`, `validation_artifact_path`, and `decision_artifact_path` to use `entry_{id}` instead of `claim_{id}`.

**Phase 18 Acceptance Criteria:**

1. Searching the `src/rules_engine/` directory for "claims", "fnol", "finserv", or "underwriting" yields zero results outside of tests.
2. `named_catalog.py` no longer contains any Python imports from legacy MCP forks (like `iceberg_mcp_server_claims`).
3. Running the test suite passes, proving that the engine can load a generic pack and execute its routines without relying on the deleted legacy assertions or fallbacks.