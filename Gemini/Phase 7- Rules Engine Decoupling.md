### Phase 7: Rules Engine Decoupling and Dead Code Pruning

**To: Grok (Cursor)**
**From: Implementation Architect**

The rules of engagement remain in force. The legacy `ins_claims_agent` package was successfully relocated to `src/`, but it currently violates the domain-agnostic architecture invariant by retaining domain-specific naming ("claims") and legacy documentation ("Cloudera Agent Studio"). It also contains unused surface area that the Mistral loop no longer calls.

In this phase, you will strip this package down to its essential deterministic components and rename it to reflect its role as the universal rules engine.

**Context Updates:**

* Target directory: `src/ins_claims_agent/` (to be renamed).
* The Mistral tools (`src/core/tools.py`) exclusively rely on `case_json.py`, `route_claim.py`, `cel_rules.py`, `yaml_rules.py`, `named_catalog.py`, `studio_io.py`, and `pack.py`.
* Unstructured triage is handled by the Mistral Orchestrator, making the old cosine router obsolete.
* MCP execution is handled by the subprocess wrappers and Mistral `@tool` decorators, making the old facade obsolete.

#### Task 1: Rename and Rebrand the Package

* Rename the directory `src/ins_claims_agent/` to `src/rules_engine/`.
* Open `src/rules_engine/__init__.py` and rewrite the package docstring. Remove all references to "Cloudera Agent Studio tools for claim RDF routing." Replace it with: `"Deterministic CEL rules engine and JSON graph builder for domain packs."`

#### Task 2: Prune Legacy Studio Surface Area

Delete the following files and directories from the newly renamed `src/rules_engine/` directory, as they are no longer invoked by the FastAPI loop or `src/core/tools.py`:

* Delete the `pre_router/` directory (cosine triage).
* Delete the `mcp_facade/` directory (Iceberg/Atlas/Ranger stubs).
* Delete `graph/validate_graph.py`.

#### Task 3: Refactor Imports in the Core Runtime

* Open `src/core/tools.py`.
* Find and replace all import paths referencing `ins_claims_agent`. Update them to import from `rules_engine`.
* *Example:* `from ins_claims_agent.graph.route_claim import route_claim` becomes `from rules_engine.graph.route_claim import route_claim`.


* Ensure any internal relative imports within `src/rules_engine/` (if they were absolute) are also updated to the new package name.

**Phase 7 Acceptance Criteria:**

1. The `src/ins_claims_agent` directory no longer exists.
2. `pre_router`, `mcp_facade`, and `validate_graph.py` are completely removed.
3. Running the Phase 5 Shadow Validation suite (`pytest tests/test_phase5_shadow.py`) passes 100%, proving the `rules_engine` correctly executes the JSON graph materialization and CEL playbook routing for all legacy seeds under its new namespace.

Grok:
The deterministic engine is now `src/rules_engine`. `src/ins_claims_agent` is gone.

The package docstring is “Deterministic CEL rules engine and JSON graph builder for domain packs.” `pre_router/`, `mcp_facade/`, and `graph/validate_graph.py` are removed. `src/core/tools.py` and the phase tests import `rules_engine` for the case graph, the CEL playbook, the named catalog, and the session files.

`pytest tests/` passed all 22 tests, including the shadow seeds.