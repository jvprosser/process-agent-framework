The `probe_id` and `matched_cel` are empty because of a conflict between your modernized backend and the legacy instructions still lingering in the underwriting pack's agent prompt.

There are two overlapping reasons this hallucination is happening:

1. **The Backend Strips the Payload:** In `src/core/agent_loop.py` (`_specialist_text`), the backend explicitly filters the payload it shows to the specialist LLM. It only sends `routing_summary`, `next_step`, `agent_role`, `write`, and `allowed_tools`. The model literally cannot see the `probe_id`, `matched_cel`, or `entity_id`.
2. **The Backend Overwrites the Tool Call:** In `agent_loop.py` (`_dispatch_allowed`), the engine completely ignores whatever parameters the LLM passes to `run_named_write`. The backend automatically intercepts the call and injects the *entire* original `route_json` envelope using the `_write_params` function.

Because the `UnderwritingIntakeSpecialist.md` prompt still contains legacy instructions telling the LLM to manually construct an `event_json` string with `claim_id` and `probe_id`, the LLM panics when it can't find them in its context and invents empty strings to satisfy its system prompt.

Here is the Phase 22 assignment to clean up the legacy agent prompts so the models stop trying to do the backend's job.

---

### Phase 22: Agent Prompt Modernization

**To: Grok (Cursor)**
**From: Implementation Architect**

The rules of engagement remain in force. The Python backend now automatically injects the full `route_json` envelope into `run_named_write` calls, ignoring the parameters provided by the LLM. However, our domain-specific agent prompts still contain legacy instructions ordering the models to manually construct complex `event_json` strings, causing them to hallucinate missing fields like `probe_id` and `matched_cel`.

In this phase, you will clean the legacy instructions out of the agent markdown files.

#### Task 1: Clean Underwriting Agent Prompts

* Open the markdown files in `packs/underwriting/agents/` (specifically the one for `UnderwritingIntakeSpecialist`).
* Delete any instructions that tell the model to manually extract `claim_id`, `run_id`, `playbook_version`, `matched_cel`, or `probe_id`.
* Delete any instructions dictating how to format or stringify `event_json`.
* Replace those complex tool-calling instructions with a simple, generic directive:
*"If instructed to write an event (e.g., `write_submission_decline`), call the `run_named_write` tool and pass an empty `{}` for the params. The backend orchestrator will automatically inject the routing context."*

#### Task 2: Clean Claims and Finserv Agent Prompts

* Scan the agents in `packs/claims/agents/` and `packs/finserv/agents/`.
* Remove any similar legacy instructions requiring the LLM to manually build database parameters or JSON strings for `run_named_write`.
* Ensure all agents understand they only need to pass the `label` to the tool, and the backend will handle the data payload.

#### Task 3: Commit the Prompt Updates

* Stage the modified `.md` agent files across the packs.
* Execute the commit: `git commit -m "fix(agents): remove legacy event_json construction instructions from agent prompts"`
* Push to `main`.

**Phase 22 Acceptance Criteria:**

1. Running entry `9100` through the underwriting pack results in a clean `<think>` block where the LLM simply calls `run_named_write` with an empty `{}` params object.
2. The model no longer hallucinates default IDs (like "demo-9100-e2e") or complains about missing `probe_id`s in its reasoning trace.