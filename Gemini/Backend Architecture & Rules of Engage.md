# Backend Architecture & Rules of Engagement

This document describes the Python/Iceberg backend as it runs today. Routing, lake access, and the UI contract below are invariants. When debugging or expanding the backend, these rules cannot be violated.

## 1. The engine is domain-agnostic

`src/rules_engine/`, `src/core/`, and `mcp_forks/` route and orchestrate. They do not branch on a domain, a field name, or a playbook step. Business rules, SQL, ontology, CEL type lists, and specialist prompts live exclusively in `packs/<domain>/`.

The active packs are `claims`, `underwriting`, `retirement_distributions`, and `retirement_rollovers`.

The chat path operates in three Python steps:
1. `run_orchestrator` extracts one `entity_id` from the user text. No id returns the missing-id envelope.
2. `run_intake` materializes the session entry and evaluates the playbook. `next_step`, `agent_role`, `lane`, `write`, and `allowed_tools` come *only* from that evaluation.
3. `run_specialist` runs the assigned role binding only `run_named_query` and `run_named_write`. (Even if `save_entry_artifact` appears in `allowed_tools`, it is not offered to the model).

The model does not choose the route. The codebase is 100% free of CrewAI. There are no manager agents or task hierarchies. Intake and the orchestrator use fixed standalone prompts in `src/core/agent_loop.py`. The specialist prompt is the one pack file whose stem matches `agent_role` or its snake_case form (e.g., `UnderwritingIntakeSpecialist` maps to `underwriting_intake_specialist.md`).

## 2. The API contract

`POST /api/v1/chat` accepts `user_text`, `pack_id`, and `dialect` (`impala` when omitted). The response is a Pydantic `ChatResponse` with four strict nodes:

- **`meta`**: `pack_id`, `run_id` (a new UUID for the HTTP request), `entity_id`, `terminal`, `dialect`, `debug_cot`.
- **`layout`**: fixed as `template: split_pane`, `widgets: [chat_feed, routing_summary_panel]`, `focus: chat_feed`.
- **`content`**: `chat_text`; `routing_summary` (`next_step`, `agent_role`, `lane`, `routing_reason`); `checks`; `view_data`; `session_graph`.
- **`actions`**: `quick_replies` and `tool_invocations`. Both are empty unless the matched action sets `letter_on_request: true`. In that case, `quick_replies` contains the string `"Please write the recommended letter."`, and `tool_invocations` contains an item with `intent: save_entry_artifact` and `payload.entry_id` set to the session id.

`session_graph` is the saved entry document, `entry_{id}_case.json`. `view_data` is the specialist read described in Section 5. `POST /api/v1/action` runs a named tool for the UI and does not evaluate the playbook again.

## 3. Pack layout

- **`pack.yaml`**: pack id, `entry_id_param`, ontology path, playbook path, and `graph` type lists (`strings`, `numbers`, `booleans`, `lists`). CEL coercion reads those lists. Every current pack sets `entry_id_param: claim_id`. Every `pack.yaml` explicitly defines `session_spine`, `session_signals`, and a nested `catalog:` block for fixture metadata. That block is not the SQL catalog.
- **`ontology/*.json`**: fields projected onto the session entry.
- **`playbook/playbook.yaml`**: ordered CEL probes and the action for each probe. The action supplies the step, agent, lane, write label, tools, and terminal flag.
- **`catalog/catalog.yaml`**: `database`, and for Trino a `schema`. Reads and writes are labels. A label is runnable only when it has a `sql:` file. `handler:` or `fixture:` with no `sql:` fails closed.
- **`catalog/sql/{dialect}/`**: Impala and Trino statement files. The yaml path stays `sql/<file>.sql`. The loader inserts `impala` or `trino`. Claims and underwriting have both trees. The retirement packs have no SQL tree and no schema key.
- **`agents/*.md`**: the specialist prompt for that role. If the pack has no `agents/` directory, the role gets one generic line. If the directory exists, the role must match exactly one file. This directory strictly contains only the specialist prompts assigned by the playbook. No legacy manager or orchestrator prompts exist.

## 4. Session document and SQL

Lake access is exclusively `run_named_query` and `run_named_write` by label. The model does not author SQL.

`build_session_graph` resolves the spine and signals queries strictly by reading `session_spine` and `session_signals` from the pack configuration. There is no suffix guessing or fallback logic. A pack missing either key raises a configuration error. It passes `{entry_id_param: entity_id}` to both. The builder keeps the spine object and the signals object. Another `-- result:` statement in the same file is not copied onto the entry. The entry is local session state and is not written back to Iceberg.

`database:` is substituted for `${var:database}` in both dialects. `schema:` is the Trino session schema and is required for a live Trino call. Impala still connects with `IMPALA_DATABASE` and does not read `schema:`.

`dialect` is `impala` or `trino`. Anything else is rejected. Trino statement text in this repo uses `CURRENT_TIMESTAMP`, `strpos`, `CAST(... AS VARCHAR)`, `to_unixtime(date_parse(...))`, and `array_join(array_agg(...))`. `TRINO_PASSWORD` is optional. `TRINO_CATALOG` still comes from the environment and defaults to `iceberg`. There is no `TRINO_SCHEMA` setting. `CASE WHEN EXISTS` projections remain fully supported in both dialects.

Numeric session ids are bound unquoted. `${var:database}` stays an unquoted identifier. `'{run_id}'` stays a quoted string.

## 5. Tools & Injection

The specialist may call `run_named_query` and `run_named_write`. Before the model speaks, the backend runs the first allowed tool whose label starts with `get_`, using `{entry_id_param: entity_id}`, and stores that payload on `view_data`. A later query call uses the arguments the model supplied.

The lake write always happens. If the model calls `run_named_write`, `_dispatch_allowed` sends `_write_params` and `_execute_write` does not run. `_execute_write` runs only when that write label is missing from the recorded tool calls. The backend ignores the model's empty params and constructs `_write_params` containing:
1. `run_id`: `run-{entity_id}` (used for the lake write because `evaluate_playbook` does not put a run_id on the route, unlike the HTTP UUID in `meta.run_id`).
2. `event_json`: the route object (carrying `entity_id`, `next_step`, `agent_role`, `lane`, `write`, `allowed_tools`, and other fields returned by `evaluate_playbook`).
3. `[entry_id_param]`: The active pack's ID key mapped to the entity ID (e.g., `claim_id: "9100"`). Write statements containing `${var:claim_id}` resolve successfully using this dynamically injected value.

Specialist prompts must never instruct the model to build `event_json`. 

`save_entry_artifact(entry_id, artifact_type="letter")` writes a fixed body to `entry_{id}_{type}.txt` in the session directory. `artifact_type` is reduced to letters and digits. The tool does not send the file and does not accept body text from the model. The session directory is `SESSION_DIRECTORY`, or `workspace/` in the repo when that variable is unset.