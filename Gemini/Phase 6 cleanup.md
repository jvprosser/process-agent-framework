Here is the Phase 6 cleanup assignment for Grok. You can paste this directly into Cursor to purge the legacy Cloudera Agent Studio and CrewAI footprints.

---

### Phase 6: Legacy Deprecation and Repository Cleanup

**To: Grok (Cursor)**
**From: Implementation Architect**

The new Mistral SDK and FastAPI runtime is fully operational and completely decoupled from Cloudera Agent Studio and CrewAI. The Shadow Validation suite proves the routing is identical. It is time to delete the legacy code and configuration files.

#### Task 1: Delete Root Legacy Configurations

The base claims domain has been successfully migrated to `packs/claims/`. Delete the original root-level configurations that powered the old base application:

* Delete the root `ontology/` directory.
* Delete the root `playbook/` directory.
* Delete the temporary `pack.yaml` pointer at the root (if it exists).

#### Task 2: Delete the Cloudera Agent Studio Tree

The entire `agent_studio/` directory is obsolete. The new Mistral implementation in `src/` replaces it entirely.

* Delete `agent_studio/src/ins_claims_agent/` (legacy graph builder, validation, and route evaluation).
* Delete `agent_studio/studio_tools/` (legacy Studio tools and CrewAI agent definitions like `manager_agent.md`).
* Delete `agent_studio/tests/` (legacy `test_route_claim.py`, `test_packs.py`, and `test_route_fnol.py` are superseded by `tests/test_phase5_shadow.py`).
* Delete the parent `agent_studio/` directory itself.



#### Task 3: Verify the Remaining Repository Structure

After the deletions, ensure only the required Mistral backend and data dependencies remain. The root should look exactly like this:

* `src/` (Mistral runtime, FastAPI, `PackManager`)
* `packs/` (`claims`, `underwriting`, `retirement_distributions`, `retirement_rollovers`)
* `tests/` (Phases 1-5 and shadow tests)
* `mcp_forks/` (Iceberg MCP V7 servers for Impala lake access)
* `ddl/` (Iceberg table schemas and seed SQL)
* `docs/` (Architecture ADRs and runbooks)

**Acceptance Criteria:**

1. The `agent_studio`, root `ontology`, and root `playbook` directories no longer exist.
2. Running `pytest tests/` passes 100%, proving the test suite no longer relies on the legacy `agent_studio` Python modules or the root configuration files.

---

Would you like to draft the `Dockerfile` and `docker-compose.yml` next to package the FastAPI app alongside the local Mistral inference server and MCP processes?