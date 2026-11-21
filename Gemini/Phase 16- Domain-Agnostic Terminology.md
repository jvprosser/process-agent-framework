### Phase 16: Domain-Agnostic Terminology & Graph Refactoring

**To: Grok (Cursor)**
**From: Implementation Architect**

The rules of engagement remain in force. We are removing the final vestiges of the legacy Cloudera Agent Studio architecture. The engine must be entirely use-case independent. We are stripping out all hardcoded "claim", "fnol", and "case" terminology in favor of the domain-agnostic term "entry".

In this phase, you will consolidate the graph building files, remove the legacy wrappers, and refactor the rules engine to operate strictly on "entries".

#### Task 1: File Consolidation & Renaming

Perform the following file operations within `src/rules_engine/graph/`:

* Rename `case_json.py` to `entry_json.py`.
* Rename `route_claim.py` to `route_entry.py`.
* Delete `build_claim_graph.py` (legacy stub).
* Delete `build_case_graph.py` (legacy stub).
* Create a new wrapper (if required by tools) or export directly from `entry_json.py`. For this implementation, expose the primary graph builder directly from `entry_json.py` and ensure `src/core/tools.py` imports it from there.

#### Task 2: Refactor `entry_json.py` (Formerly `case_json.py`)

* **Remove Hardcoded Domain Logic:** Delete the functions `build_claim_graph`, `build_claim_case`, and `build_fnol_case` entirely. The engine must rely solely on the pack-driven logic.
* **Rename and Generalize Pack Builder:** Rename `build_pack_case` to `build_entry_graph`.
* **Update Variables & Keys:**
* Change the primary identifier argument from `case_id` to `entry_id`.
* In the returned dictionary, replace `case_id` with `entry_id`.
* Replace `case_exists` with `entry_exists`.
* Replace `case_class` with `entry_class`.


* **Clean Up Derived Flags:** In `_fill_missing_case_flags` (rename to `_fill_missing_entry_flags`), remove explicit checks for `claim_exists` and `fnol_exists`. Instead, derive `entry_exists` dynamically (e.g., checking if the spine is populated with the primary `entry_id` or if the `spine` dictionary has keys).

#### Task 3: Refactor Evaluators (`cel_rules.py` & `yaml_rules.py`)

* **`cel_rules.py`:**
* Rename the function `flatten_case` to `flatten_entry`.
* Update the function signatures and internal variables to accept `entry: dict[str, Any]` instead of `case: dict[str, Any]`.


* **`yaml_rules.py`:**
* Update all function signatures (`eval_match`, `eval_pred`) to accept `entry: dict[str, Any]` instead of `case: dict[str, Any]`.



#### Task 4: Refactor Routing (`route_entry.py`)

* Rename the primary function `route_claim` to `route_entry`.
* Update the signature to: `def route_entry(entry: dict[str, Any], entry_id: int | str, *, playbook_path: ...)`
* Update all internal references from `case` to `entry`.
* Update `_default_disposition_reason` to be domain-agnostic, removing hardcoded insurance checks (like "total insured value" or "flood zone") and replacing it with a generic fallback reason string based on the active pack or a generic "No probes matched the entry state."

#### Task 5: Update Tool Callers (`src/core/tools.py`)

* Update the imports in `src/core/tools.py` (and any other files in `src/core/` or `src/api/`) to import `build_entry_graph` from `rules_engine.graph.entry_json`.
* Update imports for the router to pull `route_entry` from `rules_engine.graph.route_entry`.
* Update the `@tool` descriptions and parameter names to use `entry_id` instead of `entity_id` or `claim_id` where applicable, ensuring the Mistral LLM context understands the new generic terminology.

**Phase 16 Acceptance Criteria:**

1. The terms "case" and "claim" no longer exist as structural variables or file names within `src/rules_engine/graph/`.
2. The legacy wrapper files (`build_claim_graph.py` and `build_case_graph.py`) are deleted.
3. Running the standard test suite passes, proving the CEL playbook correctly evaluates the domain-agnostic `entry` payload.