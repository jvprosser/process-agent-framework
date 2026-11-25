# Backend Architecture & Rules of Engagement

This document describes the Python/Iceberg backend as it runs today. Routing, lake access, and the UI contract below are invariants. When debugging or expanding the backend, these rules cannot be violated.

## 1. The engine is domain-agnostic

`src/rules_engine/`, `src/core/`, and `mcp_forks/` route and orchestrate. They do not branch on a domain, a field name, or a playbook step. Business rules, SQL, ontology, CEL type lists, and specialist prompts live exclusively in `packs/<domain>/`.

The active packs are `claims`, `underwriting`, `retirement_distributions`, and `retirement_rollovers`.

`POST /api/v1/chat` runs this sequence:

1. `catalog_dialect(request.dialect, pack)` resolves the dialect. An omitted `dialect` uses the pack manifest. That key defaults to `impala`. Claims sets `dialect: trino`.
2. A request that includes `entity_id` uses that id and skips `run_orchestrator`. Otherwise `run_orchestrator` extracts one id from `user_text`. No id returns the missing-id envelope. An id containing `/`, `\`, or `..` is rejected before any session file is opened.
3. `run_intake` materializes the session entry and evaluates the playbook. The result is `routing_directives` and `checks`. The model does not choose them.
4. A `blocking: true` directive is the one that runs, even when the client sent `lane`. Otherwise the directive for `request.lane` runs. A lane that is no longer active returns the directive list and a notice, and does not call a specialist. With no `lane`, the first directive runs.
5. The user text is appended to `entry_{id}_chat_{lane}.json`. `run_specialist` sees that lane's transcript only. It binds `patch_session_field`, and `run_named_query` / `run_named_write` when the directive allows them. `save_entry_artifact` is not offered to the model.
6. If `patch_session_field` ran, the post-turn hook flushes uncommitted facts once, then `build_session_graph` and `evaluate_playbook` run once more. A second specialist does not start in that HTTP response.

The model does not choose the route. The codebase is 100% free of CrewAI. Intake and the orchestrator use fixed standalone prompts in `src/core/agent_loop.py`. The specialist prompt is the one pack file whose stem matches `agent_role` or its snake_case form (e.g., `UnderwritingIntakeSpecialist` maps to `underwriting_intake_specialist.md`).

## 2. The API contract

The UI has no domain logic. It renders `layout`, `routing_directives`, `lane_messages`, `checks`, `session_graph`, `view_data`, and `actions`. A blocking directive is the only item in `routing_directives`. Each non-blocking directive is one concurrent lane.

`POST /api/v1/chat` accepts:

- `user_text` and `pack_id`, both required.
- `dialect`: `impala`, `trino`, or omitted. Omitted uses the pack manifest.
- `entity_id` and `lane`, both omitted on the first turn and on a context switch so `run_orchestrator` runs. A follow-up sends both.

The response is a Pydantic `ChatResponse` with four nodes. These fields are always present:

- **`meta`**: `pack_id`; `run_id` (a new UUID for the HTTP request); `entity_id` (`null` on the missing-id envelope); `active_lane` (the lane whose specialist spoke, otherwise `null`); `terminal`; `dialect` (`impala` or `trino`); `debug_cot` (a list of strings).
- **`layout`**: `template: split_pane`, `widgets: [chat_feed, routing_summary_panel]`, `focus: chat_feed`.
- **`content`**: `chat_text`; `lane_messages`; `routing_summary`; `routing_directives`; `checks`; `view_data` (an object); `session_graph` (an object).
- **`actions`**: `quick_replies` (a list of strings) and `tool_invocations`. Both are empty unless the projected directive sets `letter_on_request: true`. In that case, `quick_replies` contains `"Please write the recommended letter."`, and `tool_invocations` contains one item with `intent: save_entry_artifact`, a label, and `payload.entry_id` set to the session id.

`routing_summary` is always an object. Its `next_step`, `agent_role`, `lane`, and `routing_reason` are strings or `null`. `routing_directives` items carry `directive_id`, `lane`, `agent_role`, `next_step`, `routing_reason`, `blocking`, `terminal`, `write` (string or `null`), `allowed_tools`, and `letter_on_request`. A `lane_messages` item has `role` (`user` or `assistant`), `agent_role` (string or `null`), `lane`, `text`, and an ISO-8601 UTC `timestamp`. A check has `probe_id`, `title`, `status`, and `detail`. `status` is `assigned`, `did_not_apply`, or `skipped`. `skipped_reason` is present only for `lane_already_matched`.

`routing_summary` and `meta.terminal` come from the directive list after the optional re-evaluation: the blocking gate if one is present, otherwise the requested lane, otherwise `routing_directives[0]`. `chat_text` stays the specialist who already spoke. The missing-id envelope sets `entity_id` to `null`, leaves `routing_directives` empty, and does not clear a case the client already holds. `session_graph` is `entry_{id}_case.json`. `view_data` is the specialist read described in Section 6.

`POST /api/v1/action` accepts `pack_id`, `intent`, and `payload`. It runs that tool and does not evaluate the playbook. The response is `status` (`success`) and `artifact` (the saved filename, or `""` when the tool returns none). An unknown intent or a missing payload field is HTTP 400. An unknown pack is HTTP 404.

## 3. Pack layout

- **`pack.yaml`**: pack id, `entry_id_param`, ontology path, playbook path, and `graph` type lists (`strings`, `numbers`, `booleans`, `lists`). CEL coercion reads those lists. Every current pack sets `entry_id_param: claim_id`. Every `pack.yaml` explicitly defines `session_spine`, `session_signals`, and a nested `catalog:` block for fixture metadata. That block is not the SQL catalog. Claims and underwriting also set `fact_assertion_write` and `session_assertions`. The retirement packs omit both.
- **`ontology/*.json`**: fields projected onto the session entry. `patch_session_field` accepts only these names.
- **`playbook/playbook.yaml`**: ordered CEL probes (`probes[id].cel`) and the action for each probe (`actions[id].when` is `ASK_TRUE` or `ASK_FALSE`). Every action, `default_action`, and `exception_review` when present must set `blocking` (a boolean) and a non-empty `lane`. A missing key fails at playbook load.
- **`catalog/catalog.yaml`**: `database`, and for Trino a `schema`. Reads and writes are labels. A label is runnable only when it has a `sql:` file. `handler:` or `fixture:` with no `sql:` fails closed.
- **`catalog/sql/{dialect}/`**: Impala and Trino statement files. The yaml path stays `sql/<file>.sql`. The loader inserts `impala` or `trino`. Claims and underwriting have both trees. The retirement packs have no SQL tree and no schema key.
- **`agents/*.md`**: the specialist prompt for that role. If the pack has no `agents/` directory, the role gets one generic line. If the directory exists, the role must match exactly one file. The loader reads that file only. Claims still has `manager_agent.md`, `orchestrator_agent.md`, and `routing_agent.md`; no playbook role uses them.

## 4. Playbook evaluation

`route_entry` walks `priorities` in order. It evaluates `probes[id].cel`, then takes the action whose `when` matches that boolean. `R0.1` and `R0.4` assign work on `ASK_FALSE`.

- **`blocking: true`**: that directive replaces `routing_directives` with a one-element list, and the walk stops. Checks already recorded stay.
- **`blocking: false`**: the lane keeps its first match and the walk continues for other lanes. A later match in a lane that already has a directive is a check with `status: skipped` and `skipped_reason: lane_already_matched`.
- **No match**: `default_action` is the only directive.
- **`exception_probes`**: every exception probe is scored. If any matches, the result is one directive from `exception_review` (`blocking: true`, `lane: REVIEW`). Disposition lanes are walked only when no exception matches. Underwriting is the pack that uses this path.

Claims keeps `F0`–`F7` blocking on `FNOL`, `DENY`, or `GENERAL`. Data-quality, SIU, denial, citation review, and the default are blocking. `CLOSEOUT`, `LITIGATION`, `PD`, `BI`, `SETTLEMENT`, and `SUBROGATION` are not. Inside `PD`, a populated `incident_report_number` moves the lane from `R2.0` to `R2.1`.

## 5. Session document and SQL

Lake access is exclusively `run_named_query` and `run_named_write` by label. The model does not author SQL.

`build_session_graph` resolves the spine and signals queries strictly by reading `session_spine` and `session_signals` from the pack configuration. There is no suffix guessing or fallback logic. A pack missing either key raises a configuration error. It passes `{entry_id_param: entity_id}` to both. The builder keeps the spine object and the signals object. Another `-- result:` statement in the same file is not copied onto the entry. The entry is local session state and is not written back to Iceberg.

When `session_assertions` is set, that read overlays ontology fields from the fact table onto the entry. `entry_{id}_overrides.json` is then applied. For each sidecar field, both values are coerced with the pack `graph` lists. Equal values drop that key from the sidecar. A different sidecar value overwrites the entry and stays until the lake catches up. Packs without `session_assertions` keep the sidecar as the only stored chat fact.

`database:` is substituted for `${var:database}` in both dialects. `schema:` is the Trino session schema and is required for a live Trino call. Impala still connects with `IMPALA_DATABASE` and does not read `schema:`.

`dialect` is `impala` or `trino`. Anything else is rejected. Trino statement text in this repo uses `CURRENT_TIMESTAMP`, `strpos`, `CAST(... AS VARCHAR)`, `to_unixtime(date_parse(...))`, and `array_join(array_agg(...))`. `TRINO_PASSWORD` is optional. `TRINO_CATALOG` still comes from the environment and defaults to `iceberg`. There is no `TRINO_SCHEMA` setting. `CASE WHEN EXISTS` projections remain fully supported in both dialects.

Numeric session ids are bound unquoted. `${var:database}` stays an unquoted identifier. `'{run_id}'` stays a quoted string.

## 6. Tools and injection

The specialist may call `patch_session_field`, `run_named_query`, and `run_named_write`. `patch_session_field(field, value)` rejects a name that is not in the pack ontology, and rejects `entry_exists`, `next_step`, `agent_role`, `lane`, `write`, `allowed_tools`, and `run_id`. The sidecar row stores the raw value plus `asserted_by_role`, `lane`, `http_run_id` (the request UUID), `asserted_at`, and `committed_to_lake: false`. The model does not author that metadata.

Before the model speaks, the backend runs the first allowed tool whose label starts with `get_`, using `{entry_id_param: entity_id}`, and stores that payload on `view_data`. A later query call uses the arguments the model supplied.

The playbook lake write always happens. If the model calls `run_named_write`, `_dispatch_allowed` sends `_write_params` and `_execute_write` does not run. `_execute_write` runs only when that write label is missing from the recorded tool calls. The backend ignores the model's empty params and constructs `_write_params` containing:

1. `run_id`: `run-{entity_id}` (the lake write id, distinct from the HTTP UUID in `meta.run_id`).
2. `event_json`: the route object.
3. `[entry_id_param]`: the pack's id key mapped to the entity id (e.g., `claim_id: "9100"`).

That payload is unchanged for playbook writes such as `create_pd_task`. Fact persistence is a different label, `fact_assertion_write`, and it runs only from the post-turn hook. Its parameters are `run_id`, `[entry_id_param]`, and `assertions_json`. Impala and Trino each explode that array into one append-only row per field. `committed_to_lake` becomes true only after the insert succeeds. Underwriting compares `submission_id` to `${var:claim_id}`.

Specialist prompts must never instruct the model to build `event_json`. They call `patch_session_field` with an ontology field name when the user supplies a missing case fact.

`save_entry_artifact(entry_id, artifact_type="letter")` writes a fixed body to `entry_{id}_{type}.txt` in the session directory. `artifact_type` is reduced to letters and digits. The tool does not send the file and does not accept body text from the model. The session directory is `SESSION_DIRECTORY`, or `workspace/` in the repo when that variable is unset.
