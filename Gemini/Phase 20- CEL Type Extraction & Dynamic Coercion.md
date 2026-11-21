### Phase 20: CEL Type Extraction & Dynamic Coercion

**To: Grok (Cursor)**
**From: Implementation Architect**

The rules engine itself is now cleanly decoupled from legacy catalog handlers and assertions. However, `src/rules_engine/graph/cel_rules.py` still contains hardcoded global tuples (`_STR_KEYS`, `_NUM_KEYS`, `_BOOL_KEYS`, `_LIST_KEYS`) that mix attributes from claims, finserv, and underwriting. This creates a collision risk for future packs and violates the domain-agnostic architecture.

In this phase, you will extract these hardcoded type-casting lists into their respective domain packs and refactor the CEL evaluator to coerce types dynamically.

#### Task 1: Distribute Type Keys to `pack.yaml` Files

* Open the `pack.yaml` files for all existing packs (e.g., `packs/claims/pack.yaml`, `packs/finserv/pack.yaml`, `packs/underwriting/pack.yaml`).
* Under the `graph:` key in each manifest, define four list properties: `strings`, `numbers`, `booleans`, and `lists`.
* Migrate the exact keys currently trapped in `cel_rules.py` into the appropriate domain's manifest.
* *Example (Claims `pack.yaml`):* Add `has_police_report` and `inspection_completed` to `booleans`.
* *Example (Finserv `pack.yaml`):* Add `rmd_shortfall_amount` to `numbers`.
* *Example (Underwriting `pack.yaml`):* Add `tenant_roster_review_detail` to `strings`.



#### Task 2: Refactor `cel_rules.py` for Dynamic Type Casting

* Open `src/rules_engine/graph/cel_rules.py` (or `cel_rules_2.py` if renamed).
* Delete the global tuples: `_LIST_KEYS`, `_STR_KEYS`, `_NUM_KEYS`, and `_BOOL_KEYS`.
* Update the `flatten_entry` function signature to accept the active pack's graph configuration: `def flatten_entry(entry: dict[str, Any], pack_graph: dict[str, Any]) -> dict[str, Any]:`
* Inside `flatten_entry`, replace the global tuple iterations with safe `.get()` calls against `pack_graph`:
```python
list_keys = pack_graph.get("lists") or []
str_keys = pack_graph.get("strings") or []
num_keys = pack_graph.get("numbers") or []
bool_keys = pack_graph.get("booleans") or []

```


* Update `eval_cel` to accept the `pack_graph` argument and pass it down to `flatten_entry`.

#### Task 3: Update Evaluator Call Sites

* Open `src/rules_engine/graph/route_entry.py`.
* Update `route_entry` and internal functions (like `_exec_probe`) to accept the `pack.graph` dictionary.
* Ensure that when `_exec_probe` calls `eval_cel`, it explicitly passes the active pack's graph configuration so the CEL evaluator knows exactly which fields to cast to integers, strings, or booleans for that specific domain.

**Phase 20 Acceptance Criteria:**

1. Searching the `src/rules_engine/` directory for domain-specific attributes like `rmd_shortfall_amount` or `tenant_roster_review_detail` yields zero results.
2. `cel_rules.py` dynamically coerces variable types using the `graph` definitions from the active pack.
3. Running the test suite passes, proving CEL evaluations evaluate correctly against the generic `entry` dictionary without the hardcoded global tuples.