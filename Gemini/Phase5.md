### Phase 5: Secondary Tooling, Artifacts, and Shadow Validation

**To: Grok (Cursor)**
**From: Implementation Architect**

The rules of engagement remain in force. The core Mistral engine, deterministic playbook router, and primary UI chat envelope are now complete.

In this final core phase, you will build the secondary execution path for UI-triggered actions and implement the legacy file-writing tools. Finally, you will establish the Shadow Validation suite to prove the new Mistral engine matches the legacy CrewAI routing exactly for all proven data seeds.

**Context Updates:**

* Target files: `src/api/action.py`, `src/core/tools.py`, and `tests/test_phase5_shadow.py`.
* The legacy architecture includes a tool called `save_claim_letter` that drafts a `.txt` artifact to the session directory when a user explicitly requests a recommended letter (or when an SMS is generated).


* The UI needs a distinct endpoint to trigger these specific tool invocations without running the full conversational playbook loop.

#### Task 1: The UI Action Endpoint

Create `src/api/action.py` and register it in `src/main.py`.

* Define the Pydantic request model: `ActionRequest(pack_id: str, intent: str, payload: dict)`.
* Create the POST endpoint `/api/v1/action`.
* This endpoint bypasses the Orchestrator and Intake playbook. It directly maps the `intent` string (e.g., `"save_claim_letter"`) to the corresponding Mistral `@tool` in `src/core/tools.py`.
* Execute the tool using the provided `payload` arguments and return a simple success confirmation to the UI (e.g., `{"status": "success", "artifact": "filename.txt"}`).

#### Task 2: Implement `save_claim_letter` Tool

Update `src/core/tools.py`.

* Create the `@tool` `save_claim_letter(entity_id: str, letter_type: str)`.
* This function must write a text file to the local session directory (`SESSION_DIRECTORY` / `/workspace`).
* File naming convention: If the step is an SMS (like Collect Incident Report Number), write `claim_{entity_id}_sms.txt`. For standard litigation or police requests, write `claim_{entity_id}_letter.txt`.


* The tool simply writes a templated or canned string to the disk; it does not send an actual email or SMS.



#### Task 3: Shadow Validation Suite

Create `tests/test_phase5_shadow.py` to ensure the Mistral implementation achieves 100% parity with the legacy CrewAI routing paths.

* Write parameterized tests asserting that the `evaluate_playbook` output exactly matches the legacy `next_step` for these specific seeds:
* **Seed 401**: Expect PD routing (`RequestPoliceReport` or `CollectIncidentReportNumber`).


* **Seed 402**: Expect Litigation routing (`EscalateDiscovery`).


* **Seed 403**: Expect `CloseoutAudit` (Status CLOSED).


* **Seed 404**: Expect `DenyUnlawfulOperation` or `DenyAudit` (Status DENIED).


* **Seed 88**: Expect FNOL `ConvertFnolToIncidentOnly`.


* **Seed 7001**: Expect `ProcessDistribution` (Retirement Distributions pack).





**Phase 5 Acceptance Criteria:**

1. The UI can POST to `/api/v1/action` with intent `save_claim_letter` for claim 401, resulting in HTTP 200 and the creation of `claim_401_letter.txt` in the session directory.
2. The Shadow Validation suite passes 100% against the fixture JSONs for the documented seeds (401, 402, 403, 404, 88, and 7001), proving that the CEL playbook running under Mistral produces the exact same routing JSON as the legacy architecture.