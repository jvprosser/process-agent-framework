# Architecture and patterns

High-level view for data engineers. This stack routes operational cases (auto claims, retirement distributions and rollovers, and underwriting submissions) on Cloudera: Iceberg via Impala, a Git-reviewed JSON case schema, and a YAML playbook. A local Mistral model is the natural-language front door and tool orchestrator. The CEL playbook chooses `next_step`, `agent_role`, and `lane`. FastAPI returns a four-node JSON envelope for the UI. Decision record: [ADR 0002](adr/0002-migration-to-mistral-agent-sdk.md).

Related: [ADR 0001](adr/0001-agent-studio-mcp-and-tool-layout.md), [ADR 0002](adr/0002-migration-to-mistral-agent-sdk.md), [MCP fork charter](mcp-fork-charter.md), [Atlas/Ranger plan](atlas-ranger-integration-plan.md), [finserv packs](finserv-pattern-pack.md), [probe/action test prompts](probe-action-tests.md), [RDF/SPARQL restore](rdf-sparql-branch.md).

## Problem split

Three different jobs are easy to collapse. They are kept separate on purpose.

| Job | Question | Owner |
|---|---|---|
| Lake I/O | What rows exist for this id, and what may we write? | Iceberg + Impala + a **named-query catalog** on MCP |
| Instance meaning | What is this case, and is the spine intact? | Session case JSON + field checks |
| Next work | Which lane and specialist run next? | Git playbook + CEL probes |

Atlas (deferred) would answer “what *is* this table/column in the ontology?” Ranger (deferred) would answer “who may see or change it?” Neither stores the per-run graph or chooses `next_step`.

## Runtime topology

```text
User UI
  └─ FastAPI (/api/v1/chat, /api/v1/action)
       └─ Mistral Orchestrator     Extracts entity_id
            ├─ Intake Agent        build_session_graph, then evaluate_playbook
            └─ Specialist Agent    run_named_query (view), then run_named_write
```

The model server is local. `MISTRAL_LOCAL_URL` defaults to `http://localhost:8000/v1` (vLLM). Set it to `http://localhost:11434/v1` for Ollama. There is no cloud fallback.

**Structured intake** (pack-driven):

1. `POST /api/v1/chat` carries `pack_id` and `user_text`. `PackManager` mounts `packs/<pack_id>`.
2. The Orchestrator parses the user string and returns the alphanumeric entity id, or no id.
3. The Intake Agent runs `build_session_graph`, which reads the catalog spine and routing-signal labels and writes `SESSION_DIRECTORY/claim_{id}_case.json`. It then runs `evaluate_playbook`.
4. The playbook evaluates CEL against that session JSON and returns `next_step`, `agent_role`, `allowed_tools`, and `write`.
5. A Specialist Agent loads `packs/<domain>/agents/<agent_role>.md`. It runs the first allowed `get_*` view through `run_named_query`, then the playbook write through `run_named_write`, and returns a short summary.
6. The API places the route, the view JSON, and the specialist text in the four-node envelope.

A message with no entity id returns the envelope with fallback chat text and does not run intake. When the playbook sets `letter_on_request`, `actions.tool_invocations` offers `save_claim_letter`. `POST /api/v1/action` runs that tool and writes `claim_{id}_letter.txt`, or `claim_{id}_sms.txt` when `letter_type` is `sms` or `CollectIncidentReportNumber`. The file stays in the session directory.

## Pattern: abstract UI envelope

`/api/v1/chat` returns JSON with four nodes. The root is not markdown.

* **`meta`**: `pack_id`, `run_id`, `entity_id`, `terminal`.
* **`layout`**: `template`, `widgets`, `focus`. The chat route uses `split_pane`, widgets `chat_feed` and `routing_summary_panel`, focus `chat_feed`.
* **`content`**: specialist `chat_text`, CEL `routing_summary` (`next_step`, `agent_role`, `lane`, `routing_reason`), `checks`, and raw `view_data` from the allowed read.
* **`actions`**: `quick_replies` and `tool_invocations`. A tool invocation is a pre-filled body for `POST /api/v1/action` (`intent` plus `payload`), including `save_claim_letter`.

## Pattern: allow-listed lake access (not free SQL)

MCP V7 registers four tools: `get_server_info`, `list_named_queries`, `run_named_query`, `run_named_write`. `execute_query` is not registered.

The **catalog** is compiled into the MCP package (`READ_OPS` / `WRITE_OPS`). Each label has required/optional params and a Python handler that runs curated Impala. Agents discover labels via `list_named_queries` and via Goal text. Invented SQL or unknown labels fail closed.

Claims labels (live lake): `get_claim_spine`, `get_claim_routing_signals`, `get_fnol_spine`, `get_fnol_routing_signals`, `list_adjuster_open_claims`, specialist views (`get_litigation_view`, `get_bi_view`, `get_subrogation_view`, `get_pd_view`, `get_deny_view`), `get_schema`, audit writes (`write_audit_event`, `promote_audit_run`, …), `create_litigation_task`, `create_pd_task`, `deny_claim`, and FNOL writes (`deny_fnol`, `close_fnol_incident_only`, `convert_fnol_to_claim`, `review_fnol`).

Audit lands in Iceberg (`agent_run_audit` / `agent_run_evidence`), partitioned by `run_id`. Impala mode is **table-append**; `promote_audit_run` is a no-op success (`mode=table_append`). Hive WAP branches are a later fork, not this path.

Think of the catalog as a **published data product API** over the same tables you would otherwise expose as views — except the consumer is an agent, and the contract is Git-reviewed Python rather than a BI semantic layer.

## Pattern: session case JSON, Git schema

The schema is JSON in Git (`ontology/claims.json`, or a pack `ontology/*.json`). It is not stored in Atlas and not queried as a lake table.

`build_claim_graph` materializes **this run’s** case document: claim/policy/vehicle triangle fields (auto) or mapped literals/booleans (packs). Output is `SESSION_DIRECTORY/claim_{id}_case.json` (`/workspace` in Studio). That file is the only instance document. It is not written back to Iceberg.

`validate_claim_graph` checks required fields on that JSON (auto: exists / policy / vehicle / triangle). It is instance quality for routing, not a table-level ODCS contract.

Mapping from lake JSON → case JSON is code (claims builder) or `pack.yaml` field maps (generic builder). There is no runtime join from Atlas IRIs into the builder today.

## Pattern: probes + playbook as the router

`route_claim` evaluates Git playbook probes in priority order (CEL on case JSON; YAML `match` / `SELECT` still accepted). The first matching action wins (`next_step`, `agent_role`, `coworker`, `write`, `lane`, `allowed_tools`, `terminal`). Default action is human-review/wait.

Probes bind CEL expressions to the case document (`litigation_indicator`, `triangle`, …). Rules are reviewed as YAML + CEL, not as prompt text and not as Iceberg UI config. MCP named queries still project lake facts onto that document.

A pack specialist needs exactly one `agents/<agent_role>.md`. `packs/claims` has the playbook and catalog and does not yet have those prompt files. `SiuAgent`, `SettlementAgent`, and `DataQualityAgent` have no prompt file.

A **notice is not a claim**. First notice lives on `fnol` (`fnol_id` 88 / `FNOL-2026-00088`) until `convert_fnol_to_claim` inserts a `claim` row (`80000+fnol_id` → **80088**). `deny_fnol` never calls `deny_claim`. Claim chats stay `Please process claim 401.` for the seed PD file. FNOL chats are `Please process FNOL 88.` After convert, `Please process claim 80088` uses the claim playbook (PD on 80088; leave 401 alone). Convert-then-PD runbook: [fnol-path-demo.md](fnol-path-demo.md#3-convert-then-pd-claim-80088).

FNOL through payout is a series of snapshots. The claims platform (or BPA) writes Iceberg rows; this stack classifies the current snapshot. Send another chat with the same entity id after the data changes (`Please process claim 401.`). Leave `run_id` and `next_step` out of the user text. Live FNOL runbook: [fnol-path-demo.md](fnol-path-demo.md).

### Typical PD path (separate calls)

Live Studio runbook (Impala reset + Orchestrator chats on **401**): [pd-path-demo.md](pd-path-demo.md). Convert-then-PD on **80088**: [fnol-path-demo.md](fnol-path-demo.md#3-convert-then-pd-claim-80088). DENIED runbook (impairment flip on **404** + two chats, restore after): [deny-path-demo.md](deny-path-demo.md).

Each row is a later snapshot. Earlier gaps are already filled so a higher probe does not preempt. Case JSON is what `route_claim` sees after `build_claim_graph` (spine + signals). CLOSED, DENIED, insured citation, and coded exclusions (R1.1 / R1.1d / R5.2 / R6.*) win before litigation, SIU, PD gaps, and money. If `subrogation_indicator` is true and `has_subrogation_case` is false, **R4.1** `OpenSubrogationCase` wins before offer, payment, or PD review.

| When the data looks like | Case JSON input (discriminating fields) | First hit | `next_step` |
|---|---|---|---|
| Claim missing / case not built | `{"claim_exists": false}` | R0.1 | `FixDataQuality` |
| Claim exists, triangle incomplete | `{"claim_exists": true, "triangle": false, "claim_status_code": "OPEN"}` | R0.4 | `FixDataQuality` |
| No ADJUSTER role | `{"claim_exists": true, "triangle": true, "claim_status_code": "OPEN", "has_adjuster": false, "litigation_indicator": false, "has_siu_suspected": false, "subrogation_indicator": false, "coverage_type_codes": ["COLLISION"]}` | R2.3 | `AssignAdjuster` |
| No police report, no incident number, no collect SMS | `{"claim_status_code": "OPEN", "has_adjuster": true, "has_police_report": false, "has_incident_report_number": false, "has_collect_incident_sms": false, "litigation_indicator": false, "has_siu_suspected": false, "subrogation_indicator": false}` | R2.0 | `CollectIncidentReportNumber` |
| No police report, no incident number, collect SMS already sent | `{"claim_status_code": "OPEN", "has_adjuster": true, "has_police_report": false, "has_incident_report_number": false, "has_collect_incident_sms": true, "litigation_indicator": false, "has_siu_suspected": false, "subrogation_indicator": false}` | R2.0b | `WaitForIncidentNumber` |
| No police report, incident number on file | `{"claim_status_code": "OPEN", "has_adjuster": true, "has_police_report": false, "has_incident_report_number": true, "litigation_indicator": false, "has_siu_suspected": false, "subrogation_indicator": false}` | R2.1 | `RequestPoliceReport` |
| No fault determination | `{"claim_status_code": "OPEN", "has_adjuster": true, "has_police_report": true, "has_fault_determination": false, "litigation_indicator": false, "has_siu_suspected": false, "subrogation_indicator": false}` | R2.2 | `DetermineFault` |
| Offer EXTENDED | `{"claim_status_code": "OPEN", "has_adjuster": true, "has_police_report": true, "has_fault_determination": true, "has_extended_offer": true, "litigation_indicator": false, "has_siu_suspected": false, "subrogation_indicator": false}` | R3.2 | `FollowUpOffer` |
| Offer ACCEPTED, no loss payment | `{"claim_status_code": "OPEN", "has_adjuster": true, "has_police_report": true, "has_fault_determination": true, "has_extended_offer": false, "has_accepted_offer": true, "has_loss_payment": false, "litigation_indicator": false, "has_siu_suspected": false, "subrogation_indicator": false}` | R3.4 | `IssuePayment` |
| Status CLOSED | `{"claim_exists": true, "triangle": true, "claim_status_code": "CLOSED"}` | R1.1 | `CloseoutAudit` |
| Status DENIED | `{"claim_exists": true, "triangle": true, "claim_status_code": "DENIED"}` | R1.1d | `DenyAudit` |
| Insured operator cited | `{"claim_status_code": "OPEN", "insured_operators": [{"was_cited_indicator": true}]}` | R5.2 | `HumanCitationReview` |
| Unlawful-operation exclusion | `{"claim_status_code": "OPEN", "insured_operators": [{"impairment_suspected_indicator": true}]}` | R6.1 | `DenyUnlawfulOperation` |
| Excluded/unlisted operator | `{"claim_status_code": "OPEN", "insured_operators": [{"on_policy": false}]}` | R6.2 | `DenyExcludedDriver` |
| Policy not in force on loss | `{"claim_status_code": "OPEN", "policy_status_code": "LAPSED"}` | R6.3 | `DenyLapsedPolicy` |

PD steps (`CollectIncidentReportNumber`, `WaitForIncidentNumber`, `RequestPoliceReport`, `DetermineFault`, `PdClaimsReview`) use `get_pd_view` then the playbook write. `CollectIncidentReportNumber` uses `create_pd_task` and inserts `claim_outbound_message` (SMS; no carrier) unless that purpose row already exists; it saves `claim_{id}_sms.txt` when `sms_written` is true. `WaitForIncidentNumber` is `write_audit_event` only. `RequestPoliceReport` cites `incident_report_number` from `claim_police_intake`, not claim id, and sets `letter_on_request`; that session letter via `save_claim_letter` is drafted only if the user asks. Settlement steps on this path still `write_audit_event`. `IssuePayment` means settlement work is due; the payment row still has to land in `claim_payment` from the claims platform. A later intake can then hit R1.1.

Denial: CLOSED stays **approved** (Closeout). DENIED is the other terminal status. R5.2 (cited `insured_operators`) is Human Review — view + audit, no `deny_claim`. R6.* coded exclusions (impairment/license, excluded/unlisted operator, policy not in force on `loss_date`) go to Deny Agent, which `UPDATE`s `claim_status_code` to `DENIED` (`AND … NOT IN ('CLOSED','DENIED')`). Live deny smokes flip **404** only (`PA-1003`) and restore afterward. Do not use **401** for deny. Repeatable runbook: [deny-path-demo.md](deny-path-demo.md).

## Pattern: two Studio filesystems

| | Workflow Data | Session / artifacts |
|---|---|---|
| Mount | `/workflow_data` (`WORKFLOW_DATA_DIRECTORY`) | `/workspace` (`SESSION_DIRECTORY`) |
| Access | Read-only | Read-write |
| Contents | Schema JSON, playbook, `pack.yaml`, exemplars | `claim_{id}_case.json`, validation JSON, route JSON, `claim_{id}_letter.txt` (R1.2 / R2.1), `claim_{id}_sms.txt` (R2.0) |
| Scope | All tools in the workflow | Same, per run |

Thin tools: each Studio tool is `tool.py` + `requirements.txt` only. Shared logic is `ins-claims-agent` pinned from git.

MCP is a **separate** `uvx` stdio process started by the workflow engine. It inherits engine env plus the MCP registration `env` block. It does **not** get the tool-sandbox `/workflow_data` mount. Lake credentials (`IMPALA_*`) live on that MCP env, not in tool `UserParameters`.

## Pattern: domain packs (abstracted control plane)

A **pack** holds the domain logic. `src/` loads whichever pack `pack_id` names.

| Component | Path | Function |
|---|---|---|
| Ontology | `packs/<domain>/ontology/*.json` | JSON schema mapping lake rows onto the session graph |
| Router | `packs/<domain>/playbook/playbook.yaml` | CEL probes on case JSON paths, in priority order |
| Catalog | `packs/<domain>/catalog/catalog.yaml` | Allow-listed Impala labels for MCP reads and writes |
| Agents | `packs/<domain>/agents/*.md` | Specialist system prompts, one file per `agent_role` |
| Fixtures | `packs/<domain>/fixtures/` | Static JSON for offline shadow checks |

`pack.yaml` names `case_id_param`, the ontology file, and the catalog reads. Claims lives in `packs/claims`.

**Distributions (live):** Impala database `retirement_distributions` + MCP `iceberg-mcp-server-finserv` (`INS_FINSERV_MCP_V4`). Compiled labels only. Pack fixture JSON remains the golden file for `agent_studio/tests/test_packs.py` and `tests/test_phase5_shadow.py` seed **7001**.

**Underwriting (live):** Impala database `underwriting` + MCP `iceberg-mcp-server-underwriting` (`INS_UW_MCP_V1`). Compiled labels only. The session id is the submission id. Pack fixture JSON remains golden for `agent_studio/tests/test_underwriting_pack.py`. Runbook: [packs/underwriting/README.md](../packs/underwriting/README.md).

**Rollovers:** fixture JSON under `packs/retirement_rollovers/`. Details: [finserv-pattern-pack-status.md](finserv-pattern-pack-status.md).

## Pattern: agents as a thin control plane

| Agent | Tools | Job |
|---|---|---|
| Orchestrator | Structured entity extraction | Read `user_text` and return the entity id |
| Intake Agent | `build_session_graph`, `evaluate_playbook` | Materialize the session JSON and return the playbook route |
| Specialist | `run_named_query`, `run_named_write` | One allowed view, then the playbook write, then a short summary |
| UI action | `save_claim_letter` via `/api/v1/action` | Write the session letter or SMS file when the user asks |

The model reads and calls tools. Hardship, RMD, ERISA, litigation, closeout, coded denial, and underwriting appetite, referral, and price come from the playbook or the catalog.

## Governance (not on the runtime path)

| Concern | Today | Later |
|---|---|---|
| Schema → ontology bind | Implicit (SQL handlers + builder maps + Git schema JSON) | Atlas business metadata `ontology.iri` on table/column GUIDs |
| Access / masking | Impala principal on MCP; catalog fail-closed for SQL | Ranger policies; prefer tags from Atlas classifications |
| Contract / quality | MCP catalog + per-run case JSON validate | ODCS / Atlas contracts for **table** drift; do not replace instance validate |

Atlas is complementary catalog glue. It is not a triple store and not a SPARQL endpoint. Ranger does not define Claim vs LitigationCase. Session case JSON and `event_json` are outside Impala masking.

## What is intentionally not here

- Persistent triple store or SPARQL-in-MCP
- Agent-authored SQL, Ranger policies, or playbook rules
- Per-label MCP tools (labels stay catalog keys)
- Dual Atlas servers
- Claim specialist prompt files under `packs/claims/agents/`

## Terms

**Schema / case JSON**

| Term | Meaning |
|---|---|
| Schema JSON | Field list for the case document (`ontology/claims.json`). Not a lake table. |
| Case JSON | Instance document for **this run** (`claim_{id}_case.json`). |
| Triangle | Claims check that policy, vehicle, and `policy_covers_vehicle` are set. |
| Spine / `get_claim_spine` | Named query for the core claim row used to build the case JSON. |
| Session case | Per-run JSON in `SESSION_DIRECTORY`. Not written to Iceberg. |

**Playbook / route**

| Term | Meaning |
|---|---|
| Probe | YAML `ASK`/`SELECT` or CEL on case JSON paths. First playbook match wins. |
| Playbook | YAML that orders probes and names the action. Not an ops runbook. |
| `next_step` | Action id the specialist should perform. |
| `agent_role` | Playbook specialist id. The runtime loads `packs/<domain>/agents/<agent_role>.md`. |
| `coworker` | Optional playbook label kept for the legacy Role string. The Mistral runtime selects the specialist from `agent_role`. |
| `write` | Catalog write label; the specialist Goal owns the call. |
| `lane` | Routing bucket (litigation, BI, closeout, …). |
| `routing_reason` | One-sentence why this `next_step` (playbook copy, not LLM). |
| `routing_summary` | In the chat envelope, an object with `next_step`, `agent_role`, `lane`, and `routing_reason`. The playbook also builds a plain-language string of the same facts. |
| `checks` | Evaluated probes as title + status (`assigned` / `did_not_apply`) + detail. Later playbook checks are omitted with `later_checks_not_run`. |
| `reason_probe_ids` | Probe ids evaluated this snapshot (audit / tests). Do not lead the chat with these. |
| `allowed_tools` | Catalog labels or Studio tools the specialist may call. |
| `terminal` | Router says this run is done (no further specialist). |
| Structured intake | User supplied a case/claim id. Playbook probes win over cosine. |

**MCP / audit**

| Term | Meaning |
|---|---|
| Named-query catalog | Compiled allow-list: label → params → Impala handler. |
| Label | Catalog key (`get_claim_spine`). Not a registered MCP tool. |
| MCP V7 | Current four-tool surface: info, list, named read, named write. |
| `READ_OPS` / `WRITE_OPS` | The two dicts that implement that catalog. |
| `event_json` | JSON body on `write_audit_event`. Known keys become typed columns; the full object is stored in `payload_json`. |
| Table-append / `mode=table_append` | Impala writes audit rows to main tables. No Iceberg branch. |
| Hive WAP branches | Write-audit-publish on Iceberg branches. Later Hive fork; not this path. |
| `write_audit_event` | Catalog write: `INSERT` one `agent_run_audit` row. |
| `create_litigation_task` | Catalog write: `INSERT` one `litigation_task` row from `run_id` + `event_json`. |
| `create_pd_task` | Catalog write: `INSERT` one `pd_task` row (`COLLECT_INCIDENT_NUMBER` / `REQUEST_POLICE_REPORT` / `DETERMINE_FAULT` / `PD_REVIEW`) and one `agent_run_audit` receipt. `COLLECT_INCIDENT_NUMBER` inserts `claim_outbound_message` unless that purpose row already exists (`sms_already_sent`). |
| `get_deny_view` | Catalog read: operator / policy / police business columns for deny and citation review (no PK/FK). |
| `deny_claim` | Catalog write: `UPDATE claim` to `DENIED` (refuses CLOSED and already-DENIED) plus an `agent_run_audit` receipt. Not used on `DenyAudit`. |
| `promote_audit_run` | No-op success on Impala (rows already on main). |
| Routing signals / `get_claim_routing_signals` | Named query for extra facts the graph and probes need (flags, related ids). |

**Runtime / session**

| Term | Meaning |
|---|---|
| `MISTRAL_LOCAL_URL` | Local inference base URL. Default `http://localhost:8000/v1`. |
| `/api/v1/chat` | FastAPI chat route. Body is `user_text` and `pack_id`. Response is the four-node envelope. |
| `/api/v1/action` | FastAPI tool route. Body is `pack_id`, `intent`, and `payload`. Skips the orchestrator and the playbook. |
| Workflow Data | Read-only tree the legacy tools still accept: schema JSON, playbook, `pack.yaml`, exemplars. |
| `WORKFLOW_DATA_DIRECTORY` | Env for that tree (`/workflow_data`). |
| `SESSION_DIRECTORY` | Env for per-run artifacts (`/workspace` in the legacy mount). |
| Exemplars | Labeled NL snippets shipped with a pack. |
| `save_claim_letter` | Tool: write `claim_{id}_letter.txt`, or `claim_{id}_sms.txt` for an SMS `letter_type`. Does not send mail or SMS. |

**Packs / demo**

| Term | Meaning |
|---|---|
| Pack | `packs/<domain>` directory: ontology, playbook, catalog, agent prompts, and fixtures. |
| `PACK_ROOT` | Host path on the **claims** MCP for rollover (and legacy) fixtures. Do not set on `iceberg-mcp-server-finserv` or `iceberg-mcp-server-underwriting`. |
| `PACK_ID` | Rejected for the live distributions demo. Finserv MCP is a compiled catalog, not a fixture bake-in. |
| Fixture | Canned JSON for a named query. Lake-shaped payload, not a routing rule. |
| Pointer pack | `pack.yaml` that points at existing claims trees without moving them. |
| 401 | PD / subro live seed (collision). Do not use for deny. |
| 402 | Proven live auto-claims seed (Litigation e2e). Not a version number. |
| 403 | CLOSED live seed (Closeout). |
| 404 | Deny-path live seed (`PA-1003`). Impairment flip does not touch 401. |
| Finserv | Retirement distributions/rollovers demos on this control plane. |

**Governance**

| Term | Meaning |
|---|---|
| ODCS | Open Data Contract Standard. Table-level contract/quality. Does not replace per-run case JSON validate. |
| `ontology.iri` | Planned Atlas business-metadata key: table/column GUID → ontology IRI. |

## Repo map

| Path | Role |
|---|---|
| `packs/claims/` | Claims schema, playbook, and catalog |
| `ddl/hive_iceberg/` | Iceberg DDL (claims, audit, `litigation_task`, `pd_task`); claims seed includes **404** deny file (`car_insurance_claims_seed_404.sql` for already-loaded lakes) |
| `docs/pd-path-demo.md` | Repeatable PD demo on claim 401 (Impala + Orchestrator) |
| `docs/deny-path-demo.md` | Repeatable DENIED demo on claim 404 (impairment flip + Orchestrator) |
| `mcp_forks/iceberg-mcp-server-claims/` | Impala MCP V7 + named catalog |
| `src/` | Mistral runtime, FastAPI, `PackManager`, and the CEL graph and route package |
| `packs/` | Domain packs (claims, distributions, rollovers, underwriting) |
| `tests/test_phase5_shadow.py` | Playbook parity for seeds 401, 402, 403, 404, 88, and 7001 |
