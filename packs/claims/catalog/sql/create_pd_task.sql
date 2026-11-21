-- result: pd_task
INSERT INTO ${var:database}.pd_task (
  pd_task_id, claim_id, loss_event_id, task_type_code,
  task_status_code, due_date, incident_report_number, run_id, created_at
) VALUES (
  {pd_task_id},
  '${var:claim_id}',
  {loss_event_id},
  '{task_type_code}',
  'OPEN',
  CAST('{due_date}' AS DATE),
  '{incident_report_number}',
  '{run_id}',
  CURRENT_TIMESTAMP()
);

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

-- result: existing_sms
SELECT message_id, to_phone, body_text, run_id
FROM ${var:database}.claim_outbound_message
WHERE claim_id = '${var:claim_id}'
  AND purpose_code = 'COLLECT_INCIDENT_REPORT_NUMBER'
ORDER BY created_at DESC
LIMIT 1;

-- result: sms
INSERT INTO ${var:database}.claim_outbound_message (
  message_id, claim_id, channel_code, to_phone, body_text,
  purpose_code, run_id, created_at
) VALUES (
  {message_id},
  '${var:claim_id}',
  'SMS',
  '{to_phone}',
  '{body_text}',
  'COLLECT_INCIDENT_REPORT_NUMBER',
  '{run_id}',
  CURRENT_TIMESTAMP()
);
