INSERT INTO ${var:database}.agent_run_audit (
  run_id, event_ts, claim_id, event_type, next_step, agent_role, lane,
  needs_llm, terminal, reason_probe_ids, payload_json
) VALUES (
  '{run_id}',
  CURRENT_TIMESTAMP(),
  ${var:claim_id},
  '{event_type}',
  '{next_step}',
  '{agent_role}',
  '{lane}',
  '{needs_llm}',
  '{terminal}',
  '{reason_probe_ids}',
  '{payload_json}'
)
