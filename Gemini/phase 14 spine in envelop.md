
Here is the Phase 14 assignment for Grok. You can paste this directly into Cursor to expose the materialized case graph (the spine) to the UI.

---

### Phase 14: Surface Session Graph in the UI Envelope

**To: Grok (Cursor)**
**From: Implementation Architect**

The rules of engagement remain in force. To effectively debug why the CEL playbook is routing to `FixDataQuality` (probe `R0.1` failing), and to provide the frontend with the underlying data it needs to render complex widgets, we must include the materialized session JSON (the spine and routing signals) directly in the API envelope.

In this phase, you will extend the strict API contract to include the `session_graph` and read the saved graph from the local session directory before returning the response.

#### Task 1: Extend the Pydantic API Schema

* Open `src/api/chat.py`.
* Locate the Pydantic model for the `content` node (e.g., `class Content(BaseModel):`).
* Add a new dictionary field to hold the case data: `session_graph: dict = Field(default_factory=dict)`

#### Task 2: Read the Case JSON

* In `src/api/chat.py`, inside the `POST /api/v1/chat` endpoint handler, locate the logic executed *after* `run_intake` completes (since intake runs `build_session_graph`).
* Import the I/O reader from the rules engine (e.g., `from rules_engine.studio_io import get_case_path` or standard `json`).
* Construct the path to the active session graph (e.g., `SESSION_DIRECTORY / f"claim_{entity_id}_case.json"` or whichever prefix the pack uses).
* Safely attempt to read and parse this JSON file:
```python
import json
import os

session_graph = {}
session_dir = os.getenv("SESSION_DIRECTORY", "./workspace")
# Note: adjust the prefix based on your pack's naming convention (e.g., claim_, fnol_, submission_)
graph_path = os.path.join(session_dir, f"claim_{entity_id}_case.json") 

if os.path.exists(graph_path):
    with open(graph_path, 'r') as f:
        session_graph = json.load(f)

```



#### Task 3: Map to the API Envelope & Patch Regex

* Map the loaded `session_graph` into the response: `content.session_graph = session_graph`.
* *Bonus Cleanup:* The previous run still leaked a trailing `</think>` tag in `content.chat_text`. Ensure the regex in `src/core/agent_loop.py` handles unclosed or malformed tags (e.g., `re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)` and a fallback string replace for stray `</think>`).

**Phase 14 Acceptance Criteria:**

1. Submitting a prompt for Claim 401 returns the standard four-node envelope, but `content` now includes a `session_graph` key.
2. The UI test harness's right-pane JSON viewer displays the `session_graph` object.
3. If the graph is entirely empty (`{}` or `{"spine": []}`), this will immediately confirm *why* probe `R0.1` failed, isolating the issue to the Iceberg MCP output rather than the CEL playbook itself.
