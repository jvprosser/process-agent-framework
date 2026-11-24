INSERT INTO ${var:database}.claim_fact_assertions (
  claim_id,
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
  get_json_object(piece, '$.http_run_id'),
  get_json_object(piece, '$.lane'),
  get_json_object(piece, '$.agent_role'),
  get_json_object(piece, '$.field_name'),
  get_json_object(piece, '$.field_value'),
  get_json_object(piece, '$.asserted_at')
FROM (
  SELECT
    concat(
      CASE WHEN item LIKE '{%' THEN '' ELSE '{' END,
      item,
      CASE WHEN item LIKE '%}' THEN '' ELSE '}' END
    ) AS piece
  FROM (
    SELECT explode(split(
      regexp_replace(
        regexp_replace('{assertions_json}', '^\\[', ''),
        '\\]$', ''
      ),
      '\\},\\{'
    )) AS item
  ) exploded
) objects
