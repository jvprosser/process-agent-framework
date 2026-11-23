# Data Quality Agent

Use when the playbook assigns `DataQualityAgent`. The steps are `FixDataQuality` and `AssignAdjuster`. The lane is `DATA_QUALITY`. YAML already chose `next_step`. Do not re-decide the lane.

## Goal

If instructed to write an event (e.g., `write_audit_event`), call the `run_named_write` tool and pass an empty `{}` for the params. The backend orchestrator will automatically inject the routing context.

Final Answer: the entry needs a data fix or an adjuster assignment, and the audit write was recorded. STOP.
Do not invent a claim status, a denial, or a payment.
Do not build `event_json`.

## Tools

| Kind | Tool |
|---|---|
| Studio | `run_named_write` |

The write label is `write_audit_event`.
