INSERT INTO ${var:database}.litigation_task (
  litigation_task_id, litigation_case_id, claim_id, task_type_code,
  task_status_code, due_date, run_id, created_at
) VALUES (
  {litigation_task_id},
  {litigation_case_id},
  '${var:claim_id}',
  '{task_type_code}',
  'OPEN',
  CAST('{due_date}' AS DATE),
  '{run_id}',
  CURRENT_TIMESTAMP()
)
