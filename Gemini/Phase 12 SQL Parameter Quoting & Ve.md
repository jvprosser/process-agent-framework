### Phase 12: SQL Parameter Quoting & Version Control

**To: Grok (Cursor)**
**From: Implementation Architect**

The rules of engagement remain in force. The diagnostic logs have successfully isolated the data pipeline failure: the `entity_id` extracted by the Mistral Orchestrator is treated as a string, but the SQL placeholders are unquoted. Because Impala enforces strict typing on Iceberg tables, `WHERE claim_id = 401` (integer) against a `STRING` column returns 0 rows or fails silently, resulting in an empty JSON spine.

In this phase, you will fix the SQL templates and finalize the migration with a Git commit.

**Context Updates:**

* Target files: All `.sql` files in `packs/claims/catalog/` (and any other domain catalogs).
* The deployment is stable enough to commit.

#### Task 1: Fix SQL Variable Quotes

* Open all `.sql` files in `packs/claims/catalog/` (e.g., `get_claim_spine.sql`, `get_claim_routing_signals.sql`).
* Locate the variable placeholders like `${var:claim_id}` and `{run_id}`.
* Wrap them in single quotes so Impala interprets them as strings.
* *Change:* `WHERE claim_id = ${var:claim_id}`
* *To:* `WHERE claim_id = '${var:claim_id}'`


* Ensure that any `run_id` substitutions (e.g., in `write_audit_event` or `create_pd_task`) are similarly quoted: `VALUES ('{run_id}', ...)`

#### Task 2: Commit and Push

The backend migration from legacy Cloudera Agent Studio/CrewAI to the domain-agnostic Mistral Agent SDK is complete, debugged, and functioning on local inference.

* Stage all deletions from Phase 6, the directory renames from Phase 7, the new FastAPI and UI code from Phases 8-9, and the SQL extraction from Phases 10-12.
* Execute the commit: `git commit -m "feat: complete mistral sdk migration, implement abstract packs, and wire fastAPI UI harness"`
* Push the branch to the remote repository.

**Phase 12 Acceptance Criteria:**

1. The repository is pushed and the local working tree is clean.
2. Restarting `serve.py` (or `src/main.py`) and submitting Claim 401 executes successfully.
3. The server stdout logs confirm `get_claim_spine` receives `'401'` (quoted) and returns the expected rows from Impala.
4. The right-pane JSON viewer in the UI confirms the `content.checks` properly evaluate against the populated case graph rather than routing to `FixDataQuality` due to a missing spine.