### Phase 23.1: Dynamic Trino Schema Derivation

**To: Grok (Cursor)**
**From: Implementation Architect**

The rules of engagement remain in force. In our recent multi-dialect expansion, the Trino implementation relied on a global `TRINO_SCHEMA` environment variable. This breaks our domain isolation because the database schema varies entirely by pack (e.g., Claims uses `car_insurance_claims` while Underwriting uses its own schema).

In this phase, you will remove the global environment variable and derive the Trino schema dynamically from the active pack's catalog configuration.

#### Task 1: Define Schemas in Pack Catalogs

* Open `packs/claims/catalog/catalog.yaml`. Add a root-level property defining its schema: `schema: car_insurance_claims`.
* Open `packs/underwriting/catalog/catalog.yaml`. Add a root-level property defining its specific schema (e.g., `schema: commercial_underwriting` or the exact schema name the legacy impala queries used).
* *(Note: The Retirement packs currently lack SQL files, but you may add their schemas if known, or leave them omitted).*

#### Task 2: Update the MCP Connection Factory

* Open the database connection factory inside the MCP server where the Trino client is instantiated (e.g., `mcp_forks/iceberg-mcp-server/src/iceberg_mcp_server/catalog.py` or the connection helper).
* Remove the `os.getenv("TRINO_SCHEMA")` call completely.
* Update the connection logic to accept the active pack's `schema` value, passed down from the loaded `catalog.yaml`.
* Inject this dynamic value directly into the Trino client initialization: `trino.dbapi.connect(..., catalog=os.getenv("TRINO_CATALOG"), schema=pack_schema)`.

#### Task 3: Clean the Environment Config

* Search for `TRINO_SCHEMA` across the codebase (e.g., in `.env` templates, `docker-compose.yml` stubs, `paths.py`, or config loaders).
* Delete all references to it. The system must strictly rely on the pack-provided schema.

#### Task 4: Verify Multi-Schema Execution

* Stage the updated `catalog.yaml` files and the modified MCP database client layer.
* Execute the commit: `git commit -m "fix(trino): derive database schema dynamically from pack catalog to support multi-domain endpoints"`
* Run the test suite to ensure the existing tests across Claims and Underwriting execute successfully under the `trino` dialect without connection or missing schema errors.