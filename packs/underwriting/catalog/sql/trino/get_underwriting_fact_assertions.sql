-- result: assertions
SELECT
  field_name,
  field_value
FROM (
  SELECT
    field_name,
    field_value,
    ROW_NUMBER() OVER (
      PARTITION BY field_name
      ORDER BY asserted_at DESC, write_run_id DESC
    ) AS rn
  FROM ${var:database}.underwriting_fact_assertions
  WHERE submission_id = ${var:claim_id}
) ranked
WHERE rn = 1
