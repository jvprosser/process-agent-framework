Phase 23: Multi-Dialect SQL Architecture (Trino & Impala)
To: Grok (Cursor)From: Implementation Architect

We are expanding the unified iceberg-mcp-server to support multiple backend database dialects concurrently. The engine must dynamically route reads and writes to the correct SQL syntax and database client based on the requested endpoint, starting with Trino alongside our existing Impala support.

Task 1: SQL Directory Restructuring
Isolate the existing Impala queries and establish the Trino path structure to prevent dialect collisions.

Within each pack's catalog/sql/ directory (Claims, Finserv, Underwriting), create two subdirectories: impala/ and trino/.

Move all existing .sql files into the impala/ subdirectories.

Copy those exact files into the trino/ subdirectories as the starting point for the new dialect.

Task 2: Dynamic Dialect Routing (catalog.yaml & Binder)
The rules engine and MCP server must dynamically resolve the correct SQL file based on the target endpoint.

Catalog Loader: Update the SQL path resolver in mcp_forks/iceberg-mcp-server/src/iceberg_mcp_server/catalog.py (or equivalent loader). If a pack's catalog.yaml defines sql: get_claim_spine.sql, the loader must inject the active dialect into the path: packs/{domain}/catalog/sql/{dialect}/get_claim_spine.sql.

Parameter Binding: Update the execution payload and tool definitions to accept a dialect parameter (e.g., dialect="trino" or dialect="impala"). Default to impala if omitted to preserve backward compatibility with the existing test suite.

Task 3: Database Client Factory
The MCP server must maintain concurrent support for both database engines.

Add trino-python-client to the MCP server's dependencies.

Implement a connection factory in the MCP server's execution layer. When a tool call is received, inspect the dialect parameter and route the statement to either the existing Impala client or a newly instantiated Trino client.

Ensure environment variables support concurrent configurations (e.g., IMPALA_HOST, TRINO_HOST, TRINO_USER).

Task 4: Trino Dialect Translation
Adjust the copied files in the trino/ directories to comply with Trino's ANSI-strict parser.

Scalar Subqueries: Leave the inline CASE WHEN EXISTS (...) projections exactly as they are. Trino's optimizer natively supports this syntax.

Date Functions: Replace any Impala-specific date math (e.g., months_add(), to_timestamp()) with Trino's ANSI standard functions (e.g., date_add(), explicit CAST(... AS TIMESTAMP)).

Type Strictness: Ensure all Trino queries use explicit casts (e.g., CAST(column AS VARCHAR)) for any string-to-numeric comparisons where Iceberg enforces strict schemas.

Task 5: Validation and Execution
Update the local diagnostic harness and UI payload to allow passing a requested dialect when processing a case.

Duplicate a subset of the test suite (or parameterize the existing 39 tests) to run identically against the trino dialect endpoint, validating that the spine and signals payloads match the impala results perfectly.