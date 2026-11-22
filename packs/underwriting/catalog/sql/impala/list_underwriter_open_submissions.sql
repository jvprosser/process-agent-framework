-- result: submissions
SELECT
  s.submission_id,
  s.line_of_business,
  s.submission_status_code,
  pe.first_name,
  pe.last_name
FROM ${var:database}.submissions s
INNER JOIN ${var:database}.submission_party_role r
  ON r.submission_id = s.submission_id
 AND r.role_type_code = 'UNDERWRITER'
 AND r.is_current_assignment = TRUE
 AND r.party_id = ${var:party_id}
LEFT JOIN ${var:database}.person pe ON pe.party_id = r.party_id
WHERE UPPER(COALESCE(s.submission_status_code, '')) NOT IN ('DECLINED', 'QUOTED')
ORDER BY s.submission_id
