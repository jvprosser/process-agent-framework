### Phase 23.3: Final Architecture Anchor Corrections

**To: Grok (Cursor)**
**From: Implementation Architect**

**Context:** `@BACKEND_ARCHITECTURE.md`
Our backend architecture document has three remaining factual inconsistencies regarding `pack.yaml` schemas and the write execution fallback, along with a dropped SQL quoting rule. You will patch `BACKEND_ARCHITECTURE.md` to perfectly reflect the current repository state before we pivot to the frontend.

#### Task 1: Correct `pack.yaml` Optional Keys (Section 3)

* Update the `pack.yaml` bullet point to clarify that `session_spine`, `session_signals`, and the nested `catalog:` block are **not** present in every pack.
* Specify that only the `claims` pack uses the explicit `session_spine` and `session_signals` overrides, while the other packs omit them and rely on the suffix fallback.
* Specify that the nested `catalog:` block (used for fixture metadata, not the SQL catalog) exists in the underwriting and retirement packs, but is omitted in claims.

#### Task 2: Restore the SQL Quoting Rule (Section 4)

* At the end of Section 4, restore the explicit quoting invariant: Numeric session ids are bound unquoted, `${var:database}` stays an unquoted identifier, and `'{run_id}'` stays a quoted string.

#### Task 3: Clarify the Write Fallback Path (Section 5)

* Rewrite the write execution description to clarify that `_execute_write` is strictly a fallback mechanism.
* The lake write always happens, but if the model correctly calls `run_named_write` itself, `_dispatch_allowed` handles it using `_write_params` and `_execute_write` does not run.
* `_execute_write` runs *only* when the required write label is missing from the model's recorded tool calls.

#### Task 4: Commit the Anchor

* Save the updated `BACKEND_ARCHITECTURE.md` file.
* Commit the file directly: `git commit -m "docs: align architecture anchor with actual pack schemas and write fallback logic"`