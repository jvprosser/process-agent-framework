-- result: operators
SELECT ld.driver_role_code, ld.was_cited_indicator,
       ld.impairment_suspected_indicator, d.license_status_code,
       (pd.is_excluded_driver IS NOT NULL) AS listed_on_policy,
       COALESCE(pd.is_excluded_driver, FALSE) AS is_excluded_driver
FROM ${var:database}.loss_driver ld
LEFT JOIN ${var:database}.driver d ON d.driver_id = ld.driver_id
INNER JOIN ${var:database}.claim c ON c.claim_id = ld.claim_id
LEFT JOIN ${var:database}.policy_driver pd
  ON pd.policy_id = c.policy_id
 AND pd.driver_id = ld.driver_id
 AND pd.expiration_date IS NULL
WHERE ld.claim_id = '${var:claim_id}';

-- result: policy
SELECT c.claim_status_code, c.claim_number, p.policy_number,
       p.policy_status_code, p.effective_date, p.expiration_date,
       p.cancellation_date, le.loss_date
FROM ${var:database}.claim c
INNER JOIN ${var:database}.insurance_policy p ON p.policy_id = c.policy_id
INNER JOIN ${var:database}.loss_event le ON le.loss_event_id = c.loss_event_id
WHERE c.claim_id = '${var:claim_id}';

-- result: police
SELECT report_number, agency_name, narrative_summary
FROM ${var:database}.police_report
WHERE claim_id = '${var:claim_id}';
