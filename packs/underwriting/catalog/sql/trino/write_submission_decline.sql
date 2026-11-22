INSERT INTO ${var:database}.submission_decline (
  decline_id, submission_id, reason_code, run_id, created_at
) VALUES (
  '{run_id}',
  ${var:claim_id},
  'OUT_OF_APPETITE',
  '{run_id}',
  CURRENT_TIMESTAMP
)
