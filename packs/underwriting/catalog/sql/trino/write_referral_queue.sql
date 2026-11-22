INSERT INTO ${var:database}.referral_queue (
  referral_id, submission_id, queue_code, run_id, created_at
) VALUES (
  '{run_id}',
  ${var:claim_id},
  'SENIOR_UW',
  '{run_id}',
  CURRENT_TIMESTAMP
)
