INSERT INTO ${var:database}.data_enrichment_request (
  enrichment_id, submission_id, provider_type, run_id, created_at
) VALUES (
  '{run_id}',
  ${var:claim_id},
  'INSPECTION',
  '{run_id}',
  CURRENT_TIMESTAMP()
)
