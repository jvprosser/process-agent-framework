INSERT INTO ${var:database}.quote_issuance (
  quote_id, submission_id, rating_tier, premium_cents, run_id, created_at
)
SELECT
  '{run_id}',
  s.submission_id,
  CASE UPPER(COALESCE(l.financial_stability_tier, ''))
    WHEN 'A' THEN 'A'
    WHEN 'B' THEN 'B'
    WHEN 'C' THEN 'C'
    ELSE 'STANDARD'
  END,
  CAST(
    COALESCE(s.total_insured_value, 0) * CASE UPPER(COALESCE(l.financial_stability_tier, ''))
      WHEN 'A' THEN 15
      WHEN 'B' THEN 25
      WHEN 'C' THEN 40
      ELSE 30
    END / 100 AS BIGINT
  ),
  '{run_id}',
  CURRENT_TIMESTAMP()
FROM ${var:database}.submissions s
LEFT JOIN ${var:database}.loss_history_summary l
  ON l.submission_id = s.submission_id
WHERE s.submission_id = ${var:claim_id}
