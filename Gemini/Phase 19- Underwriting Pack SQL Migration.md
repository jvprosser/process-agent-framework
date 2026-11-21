This error perfectly illustrates the strictness of our new domain-agnostic architecture.

In Phase 18, we deleted the hardcoded Python handler maps (like `_underwriting_handlers`). The unified `iceberg-mcp-server` now expects every label in a pack's `catalog.yaml` to provide an `sql:` path. The underwriting pack's catalog is clearly still pointing to the legacy `handler: get_submission_spine` directive, which the engine no longer recognizes.

Here is the Phase 19 assignment for Grok to migrate the Underwriting pack to the pure-SQL architecture, just as we did for Claims.

---

### Phase 19: Underwriting Pack SQL Migration

**To: Grok (Cursor)**
**From: Implementation Architect**

The rules of engagement remain in force. In Phase 18, we deprecated the legacy Python MCP handlers. The `underwriting` pack is currently failing because its `catalog.yaml` still relies on these deleted handlers instead of native SQL files.

In this phase, you will migrate the Underwriting catalog to the pure-SQL architecture.

#### Task 1: Update the Underwriting Catalog YAML

* Open `packs/underwriting/catalog/catalog.yaml`.
* Locate the reads and writes sections. Replace all `handler: <name>` directives with `sql: <name>.sql`.
* *Example Change:*
From: `handler: get_submission_spine`
To: `sql: get_submission_spine.sql`


* Ensure all required parameters in the YAML accurately reflect the generic terminology (e.g., `entry_id` or `submission_id`).

#### Task 2: Extract Underwriting SQL Files

* Within `packs/underwriting/catalog/`, create the corresponding `.sql` files for each read/write operation (e.g., `get_submission_spine.sql`, `get_submission_routing_signals.sql`).
* Extract the legacy Impala queries from the old underwriting tools (previously located in `iceberg_mcp_server_underwriting`) and paste them into these `.sql` files.
* Apply the strict-typing quoting rules established in Phase 13:
* Numeric `BIGINT` placeholders must be unquoted: `submission_id = ${var:submission_id}`
* String `UUID` placeholders must be single-quoted: `run_id = '${var:run_id}'`



#### Task 3: Commit the Pack Update

* Stage the modified `catalog.yaml` and the newly created `.sql` files in the underwriting pack.
* Execute the commit: `git commit -m "feat(underwriting): migrate catalog to pure SQL architecture"`
* Push to `main`.

**Phase 19 Acceptance Criteria:**

1. Running the prompt for entry `9100` with the `underwriting` pack successfully executes the SQL and populates the `session_graph` envelope.
2. The UI `routing_summary` successfully evaluates the underwriting CEL playbook against the returned submission spine.


Grok: