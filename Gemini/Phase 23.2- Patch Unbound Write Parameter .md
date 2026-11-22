Here is the exact prompt to drop into your Backend/Debugging thread to fix the unbound parameter bug.

---

### Phase 23.2: Patch Unbound Write Parameter

**To: Grok (Cursor)**
**From: Implementation Architect**

**Context:** `@BACKEND_ARCHITECTURE.md`
We have an unbound placeholder bug on the database write path. When a specialist agent triggers a write via `run_named_write`, the backend orchestrator currently ignores the model's empty `{}` params and only injects `run_id` and `event_json`. However, domain SQL files (like those in Underwriting) still expect the primary entity identifier (e.g., `${var:claim_id}` or `${var:submission_id}`).

We must inject this ID dynamically into the write parameters using the domain-agnostic `entry_id_param` defined in the pack.

#### Task 1: Locate the Write Execution Logic

* Open the Python file that handles the agent's tool execution (likely `src/core/agent_loop.py` or the tool router where `run_named_write` is intercepted before hitting the MCP server).

#### Task 2: Inject the Entity ID

* Find the dictionary payload being constructed for the write query (where `run_id` and `event_json` are currently set).
* Retrieve the `entry_id_param` string from the active pack's configuration (this is defined in `pack.yaml`, e.g., `"claim_id"` or `"submission_id"`).
* Inject the active session's `entity_id` into the write parameters dictionary using the `entry_id_param` as the key.
* *Example Logic:* `write_params[pack_config.entry_id_param] = entity_id`

#### Task 3: Verify the Handoff

* Ensure that the final payload sent to the MCP server now includes `run_id`, `event_json`, and the dynamic ID key (e.g., `claim_id: "9100"`).

#### Task 4: Commit and Test

* Run the Underwriting or Claims test suite to ensure the write execution no longer fails with an unbound placeholder error.
* Execute the commit: `git commit -m "fix(orchestrator): inject dynamic entry_id_param into named write payload to prevent unbound SQL variables"`