-- result: spine
SELECT
  s.submission_id,
  s.submission_number,
  s.producer_id,
  s.applicant_tax_id,
  s.line_of_business,
  s.class_code,
  s.naics_code,
  s.total_insured_value,
  s.submission_timestamp,
  s.submission_status_code
FROM ${var:database}.submissions s
WHERE s.submission_id = ${var:submission_id}
LIMIT 1
