Here is the Phase 1 assignment for Grok. You can paste this directly into Cursor to initiate the development.

---

### Phase 1: Core Runtime, PackManager, and Mistral Tool Wrappers

**To: Grok (Cursor)**
**From: Implementation Architect**

The rules of engagement are in force. Do not build the agent loop or API endpoint in this phase. Your objective is to establish the domain-agnostic core runtime and wrap the existing legacy execution logic into Mistral-compatible tools.

**Context Updates:**

* The target runtime directory is `src/core/`.
* Domain query catalogs are located in the packs under `catalog/`, not `queries/`.

#### Task 1: Establish the `PackManager`

Create `src/core/pack_manager.py`. This utility must abstract all domain loading so the runtime never references specific domains (like claims or underwriting) directly.

* Implement `PackManager(pack_name: str, base_dir: str)`.
* Add methods to dynamically load the JSON schema (`ontology/*.json`), the CEL rules (`playbook/playbook.yaml`), and the allow-listed queries (`catalog/`).
* **Constraint:** Ensure all paths are relative to the active pack directory. If a pack cannot be found or is missing required folders, raise a clear `RuntimeError`.

#### Task 2: Initialize the Local Mistral Client

Create `src/core/mistral_client.py`.

* Initialize the `mistralai` client configured exclusively for local inference.
* Hardcode `server_url="http://localhost:8000/v1"` and `api_key="dummy_local_key"`.
* **Constraint:** Do not include any fallback logic to Mistral's cloud API.

#### Task 3: Develop the Mistral `@tool` Wrappers

Create `src/core/tools.py`. You will wrap the existing Python subprocess/execution logic from `agent_studio/src/ins_claims_agent/` into Mistral native `@tool` decorators.

* **`@tool` run_named_query(label: str, params: dict):** Executes the MCP V7 read catalog.
* **`@tool` run_named_write(label: str, params: dict):** Executes the MCP V7 write catalog.
* **`@tool` build_session_graph(entity_id: str):** Materializes the local session case JSON based on the `PackManager`'s active ontology map.
* **`@tool` evaluate_playbook():** Evaluates the active CEL playbook against the generated session case JSON to determine `next_step`, `agent_role`, and `allowed_tools`.
* **Constraint:** The `evaluate_playbook` tool must remain 100% deterministic (Python/CEL). The LLM simply calls the tool; it does not compute the routing output.

**Phase 1 Acceptance Criteria:**

1. `src/core/pack_manager.py` successfully mounts an existing pack (e.g., `underwriting`) and loads its playbook and catalog into memory.
2. The Mistral client instantiates without cloud authentication errors.
3. The four core Mistral `@tool` decorators are syntactically valid and successfully abstract the legacy `ins_claims_agent` logic.