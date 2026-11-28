# Backend Architecture & Rules of Engagement

This document describes the Python/Iceberg backend as it runs today. Routing, lake access, and the UI contract below are invariants. When debugging or expanding the backend, these rules cannot be violated.

## 1. The engine is domain-agnostic

`src/rules_engine/`, `src/core/`, and `mcp_forks/` route and orchestrate. They do not branch on a domain, a field name, or a playbook step. Business rules, SQL, ontology, CEL type lists, and specialist prompts live exclusively in `packs/<domain>/`.

The active packs are `claims`, `underwriting`, `retirement_distributions`, and `retirement_rollovers`.

`POST /api/v1/chat` runs this sequence:

1. `catalog_dialect(request.dialect, pack)` resolves the dialect. An omitted `dialect` uses the pack manifest. That key defaults to `impala`. Claims sets `dialect: trino`.
2. A request that includes `entity_id` uses that id and skips `run_orchestrator`. Otherwise `run_orchestrator` extracts one id from `user_text`. No id returns the missing-id envelope. An id containing `/`, `\`, or `..` is rejected before any session file is opened.
3. `run_intake` materializes the session entry and evaluates the playbook. The result is `routing_directives` and `checks`. The model does not choose them.
4. A follow-up that sends both `entity_id` and `lane` is classified before any specialist read. The classifier is deterministic and does not call the model. A field matches its ontology id, or that id with underscores read as spaces, on word boundaries. Ontology comments are not matched. A longer matched phrase discards a phrase contained inside it. Exactly one remaining field can be a fact or a question. Zero fields, two or more fields, or a command that only mentions a field is a directive. A request with no `lane`, and a turn that has no `entity_id`, skip classification.
5. A fact or a question runs on `request.lane` when that lane is an active directive, or, while a blocking gate is the only directive, when the lane is declared on an action, `default_action`, or `exception_review`. `pd` does not match `PD`. The gate stays the only item in `routing_directives` until a later evaluation says otherwise. `routing_summary`, `meta.terminal`, the letter flag, and `pending_forms` follow the final directive list: the blocking gate, otherwise the requested lane, otherwise `routing_directives[0]`. `active_lane`, `chat_text`, and `lane_messages` follow `request.lane`. The assistant line's `agent_role` is the directive for that lane, or null when that lane has no directive. An ineligible lane returns `Lane {lane} has no active directive.`, sets `active_lane` to null, and does not append the user line.
6. A fact's value is a verbatim slice of `user_text`. Strings must be non-empty. Booleans accept only `true`, `false`, `yes`, `no`, `1`, and `0`. Numbers must be finite, and `0` is a stored value. Lists must be a JSON array. `flatten_entry` is not the parser. A `derived:` field is not written; the reply is `{field} is a derived field and cannot be set directly.` A value that fails coercion is not written; the reply says the value for that field could not be coerced and was not recorded. A successful fact calls `patch_session_field`, flushes once, rebuilds the graph, and re-evaluates once. The rebuilt directive list is the one that is projected. The coerced value is passed into `synthesize_lane_response` as `recorded`. The turn does not call `run_specialist`, the pre-specialist `get_*` read, or the directive write. `view_data` stays `{}`. `debug_cot` is the intake list.
7. A question reads the field from `entry_{id}_case.json`, including derived fields. The reply is `{field} is {value}.` A missing key, null, and `""` reply `{field} is not on file yet.` `0` and `false` are values. The route, sidecar, and session graph stay as intake left them.
8. A directive keeps the specialist path. A `blocking: true` directive is the one that runs, even when the client sent `lane`. Otherwise the directive for `request.lane` runs. A lane that is no longer active returns the directive list and a notice, and does not call a specialist. With no `lane`, the first directive runs. The user text is appended to `entry_{id}_chat_{lane}.json`. `run_specialist` sees that lane's transcript only. It binds `patch_session_field`, and `run_named_query` / `run_named_write` when the directive allows them. `save_entry_artifact` is not offered to the model. `<think>`, `<thought>`, and structured thinking chunks are removed from the assistant text and stored on `debug_cot`. If `patch_session_field` ran on this turn, the pre-turn `_execute_write` fallback is skipped. The post-turn hook then flushes uncommitted facts once, and `build_session_graph` and `evaluate_playbook` run once more. A second specialist does not start in that HTTP response.

The model does not choose the route. Intake and the orchestrator use fixed standalone prompts in `src/core/agent_loop.py`. The specialist prompt is the one pack file whose stem matches `agent_role` or its snake_case form (e.g., `UnderwritingIntakeSpecialist` maps to `underwriting_intake_specialist.md`).

## 2. The API contract

The UI has no domain logic. It renders `layout`, `routing_directives`, `lane_messages`, `checks`, `session_graph`, `view_data`, and `actions`. A blocking directive is the only item in `routing_directives`. Each non-blocking directive is one concurrent lane.

`POST /api/v1/chat` accepts:

- `user_text` and `pack_id`, both required.
- `dialect`: `impala`, `trino`, or omitted. Omitted uses the pack manifest.
- `entity_id` and `lane`, both omitted on the first turn and on a context switch so `run_orchestrator` runs. A follow-up sends both.

The response is a Pydantic `ChatResponse` with four nodes. These fields are always present:

- **`meta`**: `pack_id`; `run_id` (a new UUID for the HTTP request); `entity_id` (`null` on the missing-id envelope); `active_lane` (the lane whose specialist spoke, otherwise `null`); `terminal`; `dialect` (`impala` or `trino`); `debug_cot` (a list of strings). Model `<think>`, `<thought>`, `reasoning_content`, and content parts whose type is `thinking`, `reasoning`, or `thought` are copied here and kept out of `chat_text` and `lane_messages`.
- **`layout`**: `split_pane` when there is at most one directive and no pending form (`widgets` include `chat_feed`, `routing_summary_panel`, and `rule_probes`; `focus: chat_feed`). `tile_grid` when more than one directive is present or a form is pending. `routing_summary_panel` stays on both. A pending form adds `data_entry_panel` and sets `focus` to it.
- **`content`**: `chat_text`; `lane_messages`; `routing_summary`; `routing_directives`; `checks`; `view_data` (an object); `session_graph` (an object).
- **`actions`**: `quick_replies` (a list of strings), `tool_invocations`, and `pending_forms`. The replies and tool invocations are empty unless the projected directive sets `letter_on_request: true`. In that case, `quick_replies` contains `"Please write the recommended letter."`, and `tool_invocations` contains one item with `intent: save_entry_artifact`, a label, and `payload.entry_id` set to the session id. `pending_forms` lists the forms named by the returned directives.

`routing_summary` is always an object. Its `next_step`, `agent_role`, `lane`, and `routing_reason` are strings or `null`. `routing_directives` items carry `directive_id`, `lane`, `agent_role`, `next_step`, `routing_reason`, `blocking`, `terminal`, `write` (string or `null`), `allowed_tools`, and `letter_on_request`. A `lane_messages` item has `role` (`user` or `assistant`), `agent_role` (string or `null`), `lane`, `text`, and an ISO-8601 UTC `timestamp`. A check has `probe_id`, `title`, `status`, `lane` (string or `null`), and `detail`. `status` is `assigned`, `did_not_apply`, or `skipped`. `lane` is the action's lane. A `did_not_apply` probe whose candidate actions use more than one lane has `lane: null`. Underwriting exception probes use the `exception_review` lane. `skipped_reason` is present only for `lane_already_matched`.

`active_lane` is the lane that received the user message. On a directive, `chat_text` is the synthesized sentence for that lane, written after re-evaluation, and the same sentence is appended to that lane's transcript. The specialist's own reply is copied onto `debug_cot`. On a fact, `chat_text` is that same synthesis with the coerced value passed as `recorded`, and `debug_cot` stays the intake list. On a question, `chat_text` is `{field} is {value}.` or `{field} is not on file yet.` `routing_summary`, `meta.terminal`, and the letter flag come from the directive list after the optional re-evaluation: the blocking gate if one is present, otherwise the requested lane, otherwise `routing_directives[0]`. `active_lane` and `routing_summary.lane` can differ when that re-evaluation introduces a gate. The missing-id envelope sets `entity_id` to `null`, leaves `routing_directives` empty, and does not clear a case the client already holds. `session_graph` is `entry_{id}_case.json`. `view_data` is the specialist read described in Section 6. The letter tool invocation's label is `Draft Recommended Letter`.

`POST /api/v1/action` accepts `pack_id`, `intent`, and `payload`. It runs that tool and does not evaluate the playbook. The response is `status` (`success`) and `artifact` (the saved filename, or `""` when the tool returns none). An unknown intent or a missing payload field is HTTP 400. An unknown pack is HTTP 404.

## 3. Pack layout

- **`pack.yaml`**: pack id, `entry_id_param`, ontology path, playbook path, and `graph` type lists (`strings`, `numbers`, `booleans`, `lists`). CEL coercion reads those lists. An optional `derived:` map names ontology fields whose values are CEL expressions over the coerced entry. Every current pack sets `entry_id_param: claim_id`. Every `pack.yaml` explicitly defines `session_spine`, `session_signals`, and a nested `catalog:` block for fixture metadata. That block is not the SQL catalog. Claims and underwriting also set `fact_assertion_write` and `session_assertions`. Claims derives `has_incident_report_number` from `incident_report_number != ""`. The other packs omit `derived:`. The retirement packs omit both assertion labels.
- **`ontology/*.json`**: fields projected onto the session entry. `patch_session_field` accepts only these names, and it rejects a name listed in `derived:`.
- **`playbook/playbook.yaml`**: ordered CEL probes (`probes[id].cel`) and the action for each probe (`actions[id].when` is `ASK_TRUE` or `ASK_FALSE`). The YAML keys are `agent`, `step`, and `tools`. The directive JSON names them `agent_role`, `next_step`, and `allowed_tools`. Every action, `default_action`, and `exception_review` when present must set `blocking` (a boolean) and a non-empty `lane`. A missing key fails at playbook load. A form field whose id is a `derived:` field also fails at pack load. Actions still store a `coworker` string. The loader does not read it.
- **`catalog/catalog.yaml`**: `database`, and for Trino a `schema`. Reads and writes are labels. A label is runnable only when it has a `sql:` file. `handler:` or `fixture:` with no `sql:` fails closed.
- **`catalog/sql/{dialect}/`**: Impala and Trino statement files for named reads and writes, including `get_*_fact_assertions` and `write_*_fact_assertions`. The yaml path stays `sql/<file>.sql`. The loader inserts `impala` or `trino`. It does not run DDL. The create statements are `ddl/trino/fact_assertions.sql` and `ddl/hive_iceberg/fact_assertions.sql`. Claims and underwriting have both SQL trees. The retirement packs have no SQL tree and no schema key.
- **`agents/*.md`**: the specialist prompt for that role. If the pack has no `agents/` directory, the role gets one generic line. If the directory exists, the role must match exactly one file. The loader reads that file only. A prompt tells the model that the runtime supplies the active directive and `view_data`, and that a turn which records a user fact calls `patch_session_field` and does not call `run_named_write` or re-run the pre-read. Claims still has `manager_agent.md`, `orchestrator_agent.md`, and `routing_agent.md`; no playbook role uses them.

## 4. Playbook evaluation

`route_entry` walks `priorities` in order. It evaluates `probes[id].cel`, then takes the action whose `when` matches that boolean. `R0.1` and `R0.4` assign work on `ASK_FALSE`.

- **`blocking: true`**: that directive replaces `routing_directives` with a one-element list, and the walk stops. Checks already recorded stay.
- **`blocking: false`**: the lane keeps its first match and the walk continues for other lanes. A later match in a lane that already has a directive is a check with `status: skipped` and `skipped_reason: lane_already_matched`.
- **No match**: `default_action` is the only directive.
- **`exception_probes`**: underwriting walks the same priority list. A blocking match that occurs before any exception hit still replaces `routing_directives` with that gate and stops. Exception ids are collected as the walk reaches them. Once any exception has hit, the next non-exception probe returns one `exception_review` directive (`blocking: true`, `lane: REVIEW`, `directive_id: exception_review`) and does not record that disposition. If the walk ends on exception hits, that same review directive is the result.

Claims keeps `F0`–`F7` blocking on `FNOL`, `DENY`, or `GENERAL`. Data-quality, SIU, denial, citation review, and the default are blocking. `CLOSEOUT`, `LITIGATION`, `PD`, `BI`, `SETTLEMENT`, and `SUBROGATION` are not. Inside `PD`, a populated `incident_report_number` moves the lane from `R2.0` to `R2.1`.

## 5. Session document and SQL

Lake access is exclusively `run_named_query` and `run_named_write` by label. The model does not author SQL.

`build_session_graph` resolves the spine and signals queries strictly by reading `session_spine` and `session_signals` from the pack configuration. There is no suffix guessing or fallback logic. A pack missing either key raises a configuration error. It passes `{entry_id_param: entity_id}` to both. The builder keeps the spine object and the signals object. Another `-- result:` statement in the same file is not copied onto the entry. The entry is local session state and is not written back to Iceberg.

When `session_assertions` is set, that read overlays ontology fields from the fact table onto the entry. A missing fact table (`TABLE_NOT_FOUND`, or a message that the table does not exist) is an empty overlay, and the turn still routes. Any other read failure stops the turn. `entry_{id}_overrides.json` is then applied. For each sidecar field, both values are coerced with the pack `graph` lists. Equal values drop that key from the sidecar. A different sidecar value overwrites the entry and stays until the lake catches up. Packs without `session_assertions` keep the sidecar as the only stored chat fact. After that overlay, each `derived:` expression is evaluated on the graph-coerced entry and the result is written onto that field before `entry_{id}_case.json` is saved. The computed value replaces a stale lake or sidecar value for that field.

`database:` is substituted for `${var:database}` in both dialects. `schema:` is the Trino session schema and is required for a live Trino call. Impala still connects with `IMPALA_DATABASE` and does not read `schema:`.

`dialect` is `impala` or `trino`. Anything else is rejected. Trino statement text in this repo uses `CURRENT_TIMESTAMP`, `strpos`, `CAST(... AS VARCHAR)`, `to_unixtime(date_parse(...))`, and `array_join(array_agg(...))`. `TRINO_PASSWORD` is optional. `TRINO_CATALOG` still comes from the environment and defaults to `iceberg`. There is no `TRINO_SCHEMA` setting. `CASE WHEN EXISTS` projections remain fully supported in both dialects.

Numeric session ids are bound unquoted. `${var:database}` stays an unquoted identifier. `'{run_id}'` stays a quoted string.

## 6. Tools and injection

`FACT_ASSERTIONS_ENABLED` defaults to false. The truthy values are `1`, `true`, and `yes`. While it is false, `patch_session_field` raises `Fact assertions are currently disabled.` before it writes the sidecar. `build_session_graph` skips the `session_assertions` read and the sidecar overlay, and it still recomputes `derived:` from the spine and signals. `flush_fact_assertions` does not call `fact_assertion_write`. `pending_forms` is empty, so the layout omits `data_entry_panel`. A lane-chat fact appends `Direct fact overrides are currently disabled.` and does not re-route. Questions stay read-only. A directive still runs, and the specialist tool list omits `patch_session_field`. `submit_fact_form` returns HTTP 403. `save_entry_artifact` is unchanged.

The specialist may call `patch_session_field`, `run_named_query`, and `run_named_write` when fact assertions are enabled. `patch_session_field(field, value)` rejects a name that is not in the pack ontology, rejects `entry_exists`, `next_step`, `agent_role`, `lane`, `write`, `allowed_tools`, and `run_id`, and rejects a `derived:` field with `{field} is a derived field and cannot be set directly.` `submit_fact_form` returns that rejection as HTTP 400 and does not write the sidecar. The sidecar row stores the raw value plus `asserted_by_role`, `lane`, `http_run_id` (the request UUID), `asserted_at`, and `committed_to_lake: false`. The model does not author that metadata.

Before the model speaks, the backend runs the first allowed tool whose label starts with `get_`, using `{entry_id_param: entity_id}`, and stores that payload on `view_data`. A later query call uses the arguments the model supplied.

A playbook write uses the pre-turn directive. If the model calls `run_named_write`, `_dispatch_allowed` sends `_write_params` and `_execute_write` does not run. If this turn recorded `patch_session_field`, that fallback is skipped so the stale step does not write before re-evaluation. Otherwise `_execute_write` runs when the write label is missing from the recorded tool calls. The backend ignores the model's empty params and constructs `_write_params` containing:

1. `run_id`: `run-{entity_id}` (the lake write id, distinct from the HTTP UUID in `meta.run_id`).
2. `event_json`: the route object.
3. `[entry_id_param]`: the pack's id key mapped to the entity id (e.g., `claim_id: "9100"`).

That payload is unchanged for playbook writes such as `create_pd_task`. `event_json` is the spoken route, including `view_data` once the pre-read has run. Fact persistence is a different label, `fact_assertion_write`, and it runs only from the post-turn hook. Its parameters are `run_id`, `[entry_id_param]`, and `assertions_json`. Impala and Trino each explode that array into one append-only row. The row columns are `write_run_id`, `http_run_id`, `lane`, `agent_role`, `field_name`, `field_value`, and `asserted_at`, plus `claim_id` on claims and `submission_id` on underwriting. `asserted_at` is the sidecar's UTC ISO-8601 text. `committed_to_lake` becomes true only after the insert succeeds. Underwriting writes `${var:claim_id}` into `submission_id` and reads with `submission_id = ${var:claim_id}`.

Specialist prompts must never instruct the model to build `event_json`. They call `patch_session_field` with an ontology field name when the user supplies a missing case fact.

`save_entry_artifact(entry_id, artifact_type="letter")` writes a fixed body to `entry_{id}_{type}.txt` in the session directory. `artifact_type` is reduced to letters and digits. The tool does not send the file and does not accept body text from the model. The session directory is `SESSION_DIRECTORY`, or `workspace/` in the repo when that variable is unset.
