### Phase 23.5: CrewAI Artifact Purge & Anchor Update

**To: Grok (Cursor)**
**From: Implementation Architect**

**Context:**
We are finalizing the backend cleanup by formally executing the removal of all legacy CrewAI artifacts. We previously established that `manager_agent.md` and `orchestrator_agent.md` are unread because the custom fixed prompts in `src/core/agent_loop.py` handle intake and orchestration. Leaving dead code and unused prompts in the repository creates confusion. We will delete these files and update the architecture anchor to reflect a strictly zero-CrewAI codebase.

#### Task 1: Delete Legacy Prompts

* Sweep all pack directories (`packs/claims/agents/`, `packs/underwriting/agents/`, etc.).
* Delete `manager_agent.md` and `orchestrator_agent.md` entirely.
* The `agents/` directory must now *strictly* contain only the active specialist prompts defined in the playbook.

#### Task 2: Remove Python CrewAI Wrappers (If Any)

* Check `src/rules_engine/` and `src/core/` for any leftover dead code related to CrewAI (e.g., unused agent hierarchy builders, legacy task wrappers, or CrewAI imports).
* Remove `crewai` from the project's dependencies (`requirements.txt`, `pyproject.toml`, or `Pipfile`) if it is still listed.

#### Task 3: Update the Architecture Anchor (`BACKEND_ARCHITECTURE.md`)

* **Section 1:** Update the orchestration description to explicitly state: "The codebase is 100% free of CrewAI. There are no manager agents or task hierarchies. Intake and the orchestrator use fixed standalone prompts in `src/core/agent_loop.py`."
* **Section 3:** Under the `agents/*.md` bullet point, remove the sentence explaining that `manager_agent.md` and `orchestrator_agent.md` are unread. Replace it with: "This directory strictly contains only the specialist prompts assigned by the playbook. No legacy manager or orchestrator prompts exist."

#### Task 4: Execute and Verify

* Run the test suite (44 tests) to confirm no Python code was secretly relying on the deleted Markdown files or dependencies.
* Execute the commit: `git commit -m "chore(agents): purge legacy crewai manager prompts, dependencies, and update architecture anchor"`