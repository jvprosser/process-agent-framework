### Phase 10: Catalog Handler Remediation and SQL Extraction

**To: Grok (Cursor)**
**From: Implementation Architect**

The unified MCP server successfully reads the pack catalogs for basic SQL files. However, the drop from 22 to 10 passing tests indicates that several labels failed because they relied on the legacy Python handlers that we just deleted, rather than SQL files.

In the legacy claims MCP, complex labels like `get_claim_spine`, `get_claim_routing_signals`, and `create_pd_task` executed multiple Impala statements sequentially. In this phase, you will extract the remaining SQL logic into the pack and update the unified MCP server to support multi-statement labels.

#### Task 1: Extract Remaining SQL into the Claims Pack

* Refer to the legacy documentation to recover the SQL for the missing labels.
* Migrate the SQL for `get_claim_spine` (two SELECTs), `get_claim_routing_signals` (REFRESH + CTEs + lists), and all writes (e.g., `write_audit_event`, `create_pd_task`, `deny_claim`) into `packs/claims/catalog/`.
* You may store these as `.sql` files or as lists of SQL strings directly inside `catalog.yaml`. Ensure variables like `${var:claim_id}` and `{run_id}` are preserved in the SQL text.

#### Task 2: Update MCP Server for Multi-Statement Execution

* Modify `mcp_forks/iceberg-mcp-server` to handle labels that require multiple queries.
* If a label's configuration provides a list of SQL statements (or a `.sql` file with multiple statements separated by `;`), the MCP `run_named_query` and `run_named_write` handlers must iterate through and execute them in order within the same Impala session.
* For reads, aggregate the results of the multiple SELECTs into a single JSON object (e.g., `{"spine": [...], "roles": [...]}`).
* For writes, ensure receipts (like the `agent_run_audit` INSERT appended to `create_pd_task`) are executed immediately after the primary mutation.

**Phase 10 Acceptance Criteria:**

1. The `packs/claims/catalog/` directory contains the complete SQL for all labels previously handled by Python code.
2. The unified MCP server successfully executes multi-statement labels without raising "no SQL for label" errors.
3. Running `pytest tests/` restores the full suite back to 22 passed tests.