Here is the Phase 3 assignment for Grok. You can paste this directly into Cursor to build the API layer.

---

### Phase 3: The Abstract UI API Layer

**To: Grok (Cursor)**
**From: Implementation Architect**

The rules of engagement remain in force. Phase 1 and Phase 2 are complete and verified. The core runtime (`src/core/`) now successfully extracts entity IDs, runs the deterministic playbook evaluation, and dynamically invokes the correct Specialist Agent and its write tools.

In this phase, you will build the HTTPS API layer that bridges the backend agent loop to the future abstract UI frontend.

**Context Updates:**

* Target file: `src/api/chat.py` (and `src/main.py` for the FastAPI app initialization).
* The UI expects a strictly typed JSON envelope containing exactly four primary nodes: `meta`, `layout`, `content`, and `actions`.
* Raw markdown must never be returned as the top-level response.

#### Task 1: FastAPI App and Model Schemas

Create `src/api/chat.py`.

* Define the Pydantic model for the incoming request: `ChatRequest(user_text: str, pack_id: str)`.
* Define the Pydantic models for the outgoing response enforcing the four-node structure (`meta`, `layout`, `content`, `actions`).
* In `src/main.py`, initialize a standard FastAPI application and include the `/api/v1/chat` POST endpoint.

#### Task 2: Implement the `/api/v1/chat` Endpoint

Inside the endpoint routing function, orchestrate the Phase 2 loop:

1. **Initialize Context:** Generate a unique `run_id` (UUID) for this request and instantiate `PackManager` using the provided `pack_id`.
2. **Orchestrator:** Call `run_orchestrator(request.user_text, client)`.
3. **Fallback Handle:** If no `entity_id` is returned, return a graceful JSON response (e.g., `content.chat_text = "I couldn't find an ID in your request..."`) and skip the remaining steps.
4. **Intake & Routing:** Call `run_intake(entity_id, client, pack)`. Capture the returned `route_json`.
5. **Specialist Action:** Call `run_specialist(route_json, client, pack)`. Capture the specialist's conversational text.

#### Task 3: Construct the JSON Output Envelope

Map the execution results strictly to the response schema before returning:

* **`meta`**:
* `pack_id`: Echoed from the request.
* `run_id`: The UUID generated in Task 2.
* `entity_id`: The ID extracted by the orchestrator.
* `terminal`: Boolean, mapped from `route_json.get("terminal", False)`.


* **`layout`**:
* For this phase, safely hardcode standard layout values: `template: "split_pane"`, `widgets: ["chat_feed", "routing_summary_panel"]`, `focus: "chat_feed"`.


* **`content`**:
* `chat_text`: The string output from the Specialist Agent (or Orchestrator fallback).
* `routing_summary`: Extracted from `route_json` (include `next_step`, `agent_role`, `lane`, `routing_reason`).
* `checks`: Extracted from `route_json` (the CEL evaluated probes).
* `view_data`: Empty dictionary `{}` for now (will be wired to MCP reads later).


* **`actions`**:
* `quick_replies`: `[]` (empty list for now).
* `tool_invocations`: `[]` (empty list for now).



**Phase 3 Acceptance Criteria:**

1. Running `uvicorn src.main:app` successfully starts the server.
2. Sending a POST request with `{"user_text": "Please process submission 9100", "pack_id": "underwriting"}` returns HTTP 200.
3. The response body is exactly the four-node JSON structure. The backend correctly invokes the Phase 2 logic (Intake -> `DECLINE_UNINSURABLE_CLASS` -> Specialist -> Text Summary) and nests that data inside the `content` node without leaking raw LLM markdown at the root level.

Grok:
Phase 3 is the chat API. `POST /api/v1/chat` runs the orchestrator, intake, and specialist, and returns only the four-node JSON envelope.

The request body is `user_text` and `pack_id`. The response is `meta`, `layout`, `content`, and `actions`.

- `meta` carries `pack_id`, a new `run_id`, the extracted `entity_id`, and `terminal` from the playbook.
- `layout` is `split_pane`, with widgets `chat_feed` and `routing_summary_panel`, focused on `chat_feed`.
- `content.chat_text` is the specialist's text. `routing_summary` holds `next_step`, `agent_role`, `lane`, and `routing_reason`. `checks` are the playbook probes. `view_data` is `{}`.
- `actions.quick_replies` and `actions.tool_invocations` are empty lists.
- If the message has no entity id, the same envelope comes back with `content.chat_text` set to "I couldn't find an ID in your request..." and intake is skipped.

`uvicorn src.main:app` starts. The OpenAPI document responds with HTTP 200. `tests/test_phase3_api.py` posts `{"user_text": "Please process submission 9100", "pack_id": "underwriting"}` and gets HTTP 200: `next_step` is `DECLINE_UNINSURABLE_CLASS`, the specialist text is inside `content.chat_text`, and the root object has only those four nodes.

A direct call to the running server returns HTTP 500. Nothing is listening on `localhost:8000`, so the orchestrator cannot reach the local model. The verified POST uses the same client contract with the model call substituted, and the playbook and specialist still run.