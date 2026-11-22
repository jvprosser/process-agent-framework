You are exactly right. Documenting technical debt as an "architectural standard" is a trap. If the engine is truly domain-agnostic, every pack must obey the exact same schema interface. No exceptions, no fallback paths for specific folders.

Since Claims requires explicit `session_spine` and `session_signals` keys (because it has multiple queries with those suffixes), the correct standard is to make those keys **mandatory for all packs** and rip out the suffix fallback logic entirely. Similarly, the fixture `catalog:` block should exist across all packs.

Here is the phase document to hand to Grok to enforce strict pack consistency.

---

### Phase 23.4: Pack Schema Standardization

**To: Grok (Cursor)**
**From: Implementation Architect**

**Context:**
We are eliminating technical debt and inconsistencies across our pack schemas. Currently, the backend accommodates structural differences between packs (e.g., Claims uses explicit `session_spine` keys while others rely on a suffix fallback; Underwriting has a fixture `catalog:` block while Claims does not). We are standardizing `pack.yaml` across all domains so the Python engine can rely on a single, strict contract.

#### Task 1: Standardize Spine and Signals Keys

Make explicit spine and signals routing mandatory for all domains.

* Open `packs/underwriting/pack.yaml` and add:
`session_spine: get_underwriting_spine` (or the exact label of its spine query)
`session_signals: get_underwriting_routing_signals` (or its exact signals label)
* Do the same for `packs/retirement_distributions/pack.yaml` and `packs/retirement_rollovers/pack.yaml` using their respective target labels.

#### Task 2: Remove the Suffix Fallback Logic

Now that all packs explicitly declare their read targets, the Python engine no longer needs to guess.

* Locate `build_session_graph` (or the catalog loader where the fallback lives).
* Delete the logic that searches for queries ending in `_spine` and `_routing_signals`.
* Update the function to strictly read the `session_spine` and `session_signals` properties directly from the loaded `pack.yaml`. If a pack is missing these keys, it must raise a strict configuration error.

#### Task 3: Standardize the Fixture Catalog Block

Ensure all packs share the exact same structural footprint.

* Open `packs/claims/pack.yaml` and add the nested `catalog:` block for fixture metadata so that it matches the structure found in the Underwriting and Retirement packs. (If it has no immediate fixture metadata, initialize it as an empty block or with baseline defaults).

#### Task 4: Clean the Architecture Anchor

* Open `BACKEND_ARCHITECTURE.md`.
* Update Section 3 to state: "Every `pack.yaml` explicitly defines `session_spine`, `session_signals`, and a nested `catalog:` block for fixture metadata."
* Update Section 4 to state: "`build_session_graph` resolves the spine and signals queries strictly by reading `session_spine` and `session_signals` from the pack configuration. There is no suffix guessing or fallback logic."

#### Task 5: Execute and Verify

* Run the full test suite (44 tests) to ensure the spine and signals payloads are still successfully generated for all domains now that the fallback logic is dead.
* Execute the commit: `git commit -m "refactor(packs): enforce strict pack.yaml schema consistency and remove legacy suffix fallbacks"`