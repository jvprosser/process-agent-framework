### Phase 9: Unified Iceberg MCP Server

**To: Grok (Cursor)**
**From: Implementation Architect**

The rules of engagement remain in force. Currently, the repository relies on multiple domain-specific MCP server forks (`iceberg-mcp-server-claims`, `iceberg-mcp-server-finserv`, `iceberg-mcp-server-underwriting`) that have their query catalogs compiled directly into their Python packages as `READ_OPS` and `WRITE_OPS` dictionaries.

In this phase, you will consolidate these into a single, unified `iceberg-mcp-server` that remains entirely domain-agnostic by reading its named queries dynamically from the active pack's filesystem.

**Context Updates:**

* Target directory: `mcp_forks/iceberg-mcp-server/` (to be created) and `src/core/tools.py`.
* The unified MCP server will still expose only the four allowed tools: `get_server_info`, `list_named_queries`, `run_named_query`, and `run_named_write`.
* Free SQL (`execute_query`) remains prohibited.

#### Task 1: Create the Unified MCP Server

* Create a single MCP server package at `mcp_forks/iceberg-mcp-server/`.
* Strip out all hardcoded `READ_OPS` and `WRITE_OPS` dictionaries.
* Update the server initialization sequence to accept a `PACK_DIR` environment variable (or command-line argument).
* On startup, the server must parse the SQL strings and expected parameters from `${PACK_DIR}/catalog/catalog.yaml` (or the equivalent catalog files) and bind them to the `list_named_queries`, `run_named_query`, and `run_named_write` handlers.

#### Task 2: Update FastAPI Subprocess Invocation

* Open `src/core/tools.py` in the backend runtime.
* Modify the subprocess command that spawns the MCP server stdio process. It must now point to the single `iceberg-mcp-server` executable.
* Inject the active pack directory into the MCP process environment. Retrieve this path dynamically using `PackManager.active()` (e.g., `export PACK_DIR=packs/underwriting`).

#### Task 3: Delete Legacy MCP Forks

* Delete `mcp_forks/iceberg-mcp-server-claims/`.
* Delete `mcp_forks/iceberg-mcp-server-finserv/`.
* Delete `mcp_forks/iceberg-mcp-server-underwriting/`.

**Phase 9 Acceptance Criteria:**

1. Only one MCP server exists in the `mcp_forks/` directory.
2. The unified MCP server dynamically lists and executes labels (e.g., `get_pd_view` or `get_litigation_view`) based purely on the `PACK_DIR` provided to it at runtime.
3. The Phase 5 Shadow Validation suite passes 100%, proving the unified MCP server correctly executes the legacy Impala queries across claims, distributions, and underwriting seeds without domain-specific compiled code.