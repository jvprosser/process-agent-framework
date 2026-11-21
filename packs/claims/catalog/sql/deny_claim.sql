-- result: status
SELECT claim_status_code FROM ${var:database}.claim WHERE claim_id = '${var:claim_id}';

-- result: deny
UPDATE ${var:database}.claim SET claim_status_code = 'DENIED'
WHERE claim_id = '${var:claim_id}'
  AND UPPER(COALESCE(claim_status_code, '')) NOT IN ('CLOSED', 'DENIED');

-- result: audit
INSERT INTO ${var:database}.agent_run_audit (
  run_id, event_ts, claim_id, event_type, next_step, agent_role, lane,
  needs_llm, terminal, reason_probe_ids, payload_json
) VALUES (
  '{run_id}',
  CURRENT_TIMESTAMP(),
  '${var:claim_id}',
  '{event_type}',
  '{next_step}',
  '{agent_role}',
  '{lane}',
  '{needs_llm}',
  '{terminal}',
  '{reason_probe_ids}',
  '{payload_json}'
);
