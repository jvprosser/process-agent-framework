Here is the Phase 4 assignment for Grok. You can paste this directly into Cursor.

---

### Phase 4: Specialist View Data and UI Action Bindings

**To: Grok (Cursor)**
**From: Implementation Architect**

The rules of engagement remain in force. Phase 3 successfully established the FastAPI endpoint and the strict four-node JSON envelope.

In this phase, you will enrich the API response by populating the currently empty `content.view_data`, `actions.tool_invocations`, and `actions.quick_replies`. You will also prepare the Orchestrator to interact with a real local LLM endpoint rather than a test double.

**Context Updates:**

* Target files: `src/core/agent_loop.py` and `src/api/chat.py`.
* According to the architecture, a Specialist Agent may execute one view label (a read query like `get_pd_view` or `get_litigation_view`) before executing its playbook write.
* Certain playbook routes flag that a letter is recommended (e.g., `letter_on_request: true`). The UI uses this to render action buttons.

#### Task 1: Populate `view_data` via Specialist Agent

Update `run_specialist` in `src/core/agent_loop.py`.

* Inspect the `allowed_tools` list passed in the `route_json`.
* If a read label is present (e.g., any string starting with `get_` such as `get_pd_view` or `get_litigation_view`), the Specialist Agent must invoke `run_named_query` using that label and the `entity_id`.
* Capture the raw JSON returned by this query and attach it to the `route_json` output as a new key: `view_data`.
* In `src/api/chat.py`, map this captured JSON directly into the API response at `content.view_data`.

#### Task 2: Populate Actions and Quick Replies

Update the output mapping in `src/api/chat.py`.

* Inspect the `route_json` returned by the Intake/Playbook evaluation.
* If the route data contains a flag indicating an optional user action—specifically `letter_on_request: true`—populate the `actions` node:
* Add a string to `actions.quick_replies`: `"Please write the recommended letter."`
* Add a UI binding to `actions.tool_invocations`:
```json
{
  "label": "Draft Recommended Letter",
  "intent": "save_claim_letter",
  "payload": {
    "entity_id": "<the_extracted_id>"
  }
}

```




* **Constraint:** Ensure these arrays remain empty `[]` if the playbook does not recommend a letter or secondary action for the current step.

#### Task 3: Local LLM Configuration (Preparation for Live Shadowing)

To move past the HTTP 500 errors caused by a missing local server, prepare the Mistral client configuration to point to standard local inference tools.

* In `src/core/mistral_client.py`, allow the `server_url` to be overridden by an environment variable `MISTRAL_LOCAL_URL` (defaulting to `http://localhost:8000/v1` for vLLM, but allowing `http://localhost:11434/v1` for Ollama).
* Add a system prompt to the Orchestrator Agent explicitly instructing it to return *only* the alphanumeric entity ID (e.g., "9100", "401") from the user text, and to return the string "NONE" if no ID exists. (Local models need rigid instruction to prevent conversational padding).

**Phase 4 Acceptance Criteria:**

1. Running the API tests for a route that includes a view label (e.g., `get_pd_view`) successfully executes the read query and surfaces the resulting JSON inside `content.view_data`.
2. Running the API tests for a route with `letter_on_request: true` (e.g., the `RequestPoliceReport` step) successfully populates the `actions.quick_replies` and `actions.tool_invocations` arrays.
3. The Mistral client natively accepts `MISTRAL_LOCAL_URL` so the developer can boot an Ollama or vLLM container to test actual entity extraction.



Grok:
Phase 4 fills the three empty envelope fields from the playbook, and the Mistral client can point at either local server.

The specialist runs one view read before its write. The first `allowed_tools` label that starts with `get_` is passed to `run_named_query` with the entity id, and that JSON is stored on the route as `view_data`. The chat response copies it to `content.view_data`. When no view label is allowed, `view_data` stays `{}`.

If the playbook sets `letter_on_request` to true, `actions.quick_replies` is `["Please write the recommended letter."]` and `actions.tool_invocations` contains the `Draft Recommended Letter` binding with intent `save_claim_letter` and the extracted entity id. Otherwise both lists stay empty. The underwriting decline for `9100` now shows that letter button, because that step already has the flag.

`MISTRAL_LOCAL_URL` overrides the client URL. It still defaults to `http://localhost:8000/v1`. Set it to `http://localhost:11434/v1` for Ollama. The orchestrator prompt now asks for only the alphanumeric id, or `NONE` when there is no id.

`tests/test_phase4_actions.py` and the earlier phase tests passed: 12 total. A `RequestPoliceReport` route with `get_pd_view` returns that read under `content.view_data` and fills the letter actions. A route without the letter flag leaves the action lists empty.