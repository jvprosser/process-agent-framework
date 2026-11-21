You are absolutely right to call that out—this is a classic CEL trap.

If the Impala query returns 0 rows and the spine is empty, `claim_id` would be `null`. In CEL, evaluating `null != ''` actually returns `true` (because null is technically not an empty string). This would falsely signal to the playbook that the claim exists, completely breaking the routing logic. This is exactly why boolean flags are safer than raw ID evaluations in CEL.

Your instinct to use the generic `entry` attribute is the perfect architectural compromise. In Phase 16, we explicitly instructed Grok to compute a universal `entry_exists` boolean at the root of the graph. Because this flag is generated in Python *before* CEL sees it, it is guaranteed to be a safe, strict `true` or `false`, completely eliminating the null risk.

Here is the revised Phase 17 assignment to update the playbook to use this generic boolean.

---

### Phase 17: Playbook Terminology Alignment

**To: Grok (Cursor)**
**From: Implementation Architect**

The rules of engagement remain in force. The Python rules engine has successfully achieved domain-agnostic execution by consolidating all root-level existence checks into `entry_exists`. However, the domain-specific CEL playbooks—specifically the Claims playbook—are still referencing the legacy `claim_exists` and `fnol_exists` flags, which causes probes like `R0.1` to fail over to `FixDataQuality`.

We will standardize the existence checks across all domain playbooks to use the universal, null-safe `entry_exists` flag.

#### Task 1: Update the Claims Playbook

* Open `packs/claims/playbook/playbook.yaml`.
* Locate probe `R0.1` (Claim exists in the case file). Update its CEL expression to evaluate the universal boolean:
* *Change to:* `cel: "entry_exists == true"`


* Scan the rest of the playbook for any FNOL-specific existence checks (e.g., probes checking `fnol_exists == true`) and update them to use `entry_exists == true`.

#### Task 2: Commit the Configuration Fix

* Stage the modified `playbook.yaml`.
* Execute the commit: `git commit -m "fix(claims): update playbook existence checks to use domain-agnostic entry_exists flag"`
* Push to `main`.

**Phase 17 Acceptance Criteria:**

1. Submitting the prompt for Claim `401` in the local UI harness correctly evaluates `R0.1` (`entry_exists == true`), routing to `RequestPoliceReport` (or the equivalent operational lane) rather than failing over to `FixDataQuality`.
2. The UI right-pane JSON viewer shows `R0.1` successfully evaluating and passing control to the rest of the playbook.