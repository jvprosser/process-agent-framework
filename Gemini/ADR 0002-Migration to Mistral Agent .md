## ADR 0002: Migration to Mistral Agent SDK and Abstract Pack Architecture

**Status:** Accepted
**Date:** November 20, 2026

**Context**
The legacy platform relied on Cloudera AI Agent Studio and CrewAI to route operational cases, with business logic for auto claims hardcoded into the repository root. As the platform expanded to support retirement distributions, rollovers, and underwriting, the monolithic repository and CrewAI's hierarchical role-delegation model created configuration bottlenecks. Furthermore, the future frontend requires a domain-agnostic HTTP API that can dynamically render layouts based on the active business domain, which the legacy standard I/O and CrewAI terminal outputs could not support.

**Decision**
We are replacing Cloudera AI Agent Studio and CrewAI with a custom runtime powered by the Mistral Agent SDK and FastAPI.

* **Domain Packs:** All schemas, CEL playbooks, Mistral agent prompts, and Model Context Protocol (MCP) catalogs are abstracted into isolated `packs/<domain>` directories.
* **Mistral Execution:** The runtime uses a local open-weight Mistral inference server (vLLM or Ollama), completely bypassing cloud APIs.
* **Deterministic Routing Preservation:** The Mistral Large Language Model (LLM) is restricted to natural language orchestration and tool invocation. It never computes routing; the CEL playbook remains the strict, deterministic router.


* **Strict JSON API:** The FastAPI backend exposes a `/api/v1/chat` endpoint returning a strictly typed four-node JSON envelope (`meta`, `layout`, `content`, `actions`) to drive an abstract UI shell.

**Consequences**

* **Positive:** Domains can be hot-swapped at startup via `PackManager`. The API safely separates backend agent reasoning from the client application. Local open-weight models eliminate recurring API costs.
* **Negative:** Developers must run an active local inference server (e.g., `http://localhost:8000/v1`) to execute the agent loop, requiring dedicated VRAM for local development environments.

---

## architecture.md (Mistral SDK Revisions)

Below are the updated sections for the primary `architecture.md` file to reflect the new Mistral SDK runtime.

### Runtime Topology

```text
User UI
  └─ FastAPI (/api/v1/chat)
       └─ Mistral Orchestrator     Extracts entity_id; routes NL
            ├─ Intake Agent        Executes build_session_graph & evaluate_playbook
            └─ Specialist Agent    Executes run_named_query & run_named_write

```

**Structured Intake (Pack-Driven):**

1. The API receives a request with `pack_id` and `user_text`. `PackManager` mounts the domain.
2. The Orchestrator Agent parses the user string to extract the alphanumeric entity ID.
3. The Intake Agent executes the `build_session_graph` Mistral `@tool` to materialize the local session JSON (e.g., `claim_{id}_case.json` or `submission_{id}_case.json`), followed immediately by the `evaluate_playbook` tool.
4. The playbook evaluates the CEL expressions against the session JSON to deterministically output the `next_step`, `agent_role`, `allowed_tools`, and `write` target.


5. A dynamic Specialist Agent is instantiated using the `agent_role` prompt from the active pack. It executes any allowed view queries (e.g., `get_pd_view`) via `run_named_query`, executes the terminal `write` label via `run_named_write`, and summarizes the action.


6. The API packages the route data, query views, and specialist text into the four-node UI envelope.

### Pattern: Domain Packs (Abstracted Control Plane)

A **pack** encapsulates all domain logic. The `src/` runtime contains zero domain-specific literals.

| Component | Path | Function |
| --- | --- | --- |
| **Ontology** | `packs/<domain>/ontology/*.json` | JSON schema mapping lake data to the materialized session graph. |
| **Router** | `packs/<domain>/playbook/playbook.yaml` | Probes evaluating CEL on case JSON paths in priority order.

 |
| **Catalog** | `packs/<domain>/catalog/catalog.yaml` | Allow-listed Impala SQL labels mapping to MCP execution.

 |
| **Agents** | `packs/<domain>/agents/*.md` | Mistral Specialist system prompts (e.g., `UnderwritingIntakeSpecialist`). |
| **Fixtures** | `packs/<domain>/fixtures/` | Static payloads for offline Mistral shadow validation. |

### Pattern: Abstract UI Envelope

The backend does not return raw markdown. It returns a strict JSON contract allowing a generic frontend to render domain-specific actions dynamically.

* **`meta`**: Execution context (`pack_id`, `run_id`, `entity_id`, `terminal` status).
* **`layout`**: Frontend routing instructions (`template`, required `widgets`, and component `focus`).
* **`content`**: The Specialist Agent's `chat_text`, the CEL `routing_summary` and `checks`, and the raw `view_data` JSON fetched via MCP reads.
* **`actions`**: Available `quick_replies` (suggested NL follow-ups) and `tool_invocations` (pre-filled JSON payloads for direct `/api/v1/action` POSTs, such as `save_claim_letter`).



---

These updates can be passed directly to Cursor to replace the legacy CrewAI topology and runtime descriptions in the root documentation. With the architecture documented, the next logical move is to containerize the stack or begin the UI implementation.