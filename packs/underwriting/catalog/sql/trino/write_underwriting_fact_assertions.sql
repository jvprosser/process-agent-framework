INSERT INTO ${var:database}.underwriting_fact_assertions (
  submission_id,
  write_run_id,
  http_run_id,
  lane,
  agent_role,
  field_name,
  field_value,
  asserted_at
)
SELECT
  ${var:claim_id},
  '{run_id}',
  json_extract_scalar(item, '$.http_run_id'),
  json_extract_scalar(item, '$.lane'),
  json_extract_scalar(item, '$.agent_role'),
  json_extract_scalar(item, '$.field_name'),
  json_extract_scalar(item, '$.field_value'),
  json_extract_scalar(item, '$.asserted_at')
FROM UNNEST(CAST(json_parse('{assertions_json}') AS array(json))) AS rows(item)
