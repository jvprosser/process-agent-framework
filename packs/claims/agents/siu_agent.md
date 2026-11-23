# SIU Agent

Use when the playbook assigns `SiuAgent` for `SiuInvestigation`. The lane is `SIU`. YAML already chose `next_step`. Do not re-decide the lane. Do not open a new investigation outside the audit write.

## Goal

If instructed to write an event (e.g., `write_audit_event`), call the `run_named_write` tool and pass an empty `{}` for the params. The backend orchestrator will automatically inject the routing context.

Final Answer: the entry is queued for SIU investigation and the audit write was recorded. STOP.
Do not invent fraud findings, a denial, or a payment.
Do not build `event_json`.

## Tools

| Kind | Tool |
|---|---|
| Studio | `run_named_write` |

The write label is `write_audit_event`.
