INSERT INTO ${var:database}.agent_run_evidence (
  run_id, evidence_ts, claim_id, evidence_type, probe_id,
  content_format, content_text, content_uri
) VALUES (
  '{run_id}',
  CURRENT_TIMESTAMP(),
  '{claim_id}',
  '{evidence_type}',
  '{probe_id}',
  '{content_format}',
  '{content_text}',
  '{content_uri}'
)
