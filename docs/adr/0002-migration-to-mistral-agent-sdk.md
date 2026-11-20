# ADR 0002: Migration to Mistral Agent SDK and abstract pack architecture

**Status:** Accepted  
**Date:** 2026-11-20

## Context

The legacy platform relied on Cloudera AI Agent Studio and CrewAI to route operational cases, with auto-claims logic at the repository root. Retirement distributions, rollovers, and underwriting made that single tree and CrewAI's hierarchical delegation hard to configure. The frontend needs a domain-agnostic HTTP API that can render a layout from the active domain. Studio standard I/O and CrewAI terminal text do not provide that contract.

## Decision

Replace Cloudera AI Agent Studio and CrewAI with a runtime on the Mistral Agent SDK and FastAPI.

* **Domain packs.** Schemas, CEL playbooks, agent prompts, and MCP catalogs live in `packs/<domain>`.
* **Local Mistral.** The runtime calls an open-weight model on vLLM or Ollama. It does not call the Mistral cloud API.
* **Deterministic routing.** The model orchestrates language and tools. The CEL playbook chooses `next_step`, `agent_role`, and `lane`.
* **JSON API.** `POST /api/v1/chat` returns `meta`, `layout`, `content`, and `actions`. `POST /api/v1/action` runs one UI tool call.

Runtime detail: [architecture.md](../architecture.md).

## Consequences

* **Positive.** `PackManager` mounts a domain per request. The envelope keeps agent text and UI actions in separate nodes. Local models remove per-call cloud fees.
* **Negative.** The agent loop needs a local inference server (default `http://localhost:8000/v1`) and the VRAM that server requires.
