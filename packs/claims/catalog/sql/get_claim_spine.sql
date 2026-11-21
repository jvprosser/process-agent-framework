-- result: spine
SELECT
  c.claim_id,
  c.claim_number,
  c.claim_status_code,
  c.litigation_indicator,
  c.subrogation_indicator,
  c.fraudulent_claim_indicator,
  c.total_loss_indicator,
  c.loss_event_id,
  le.loss_cause_code,
  le.loss_date,
  c.policy_id,
  p.policy_number,
  p.policy_status_code,
  p.effective_date,
  p.expiration_date,
  p.cancellation_date,
  c.insurable_object_id,
  v.vin,
  CASE WHEN pio.policy_id IS NOT NULL THEN TRUE ELSE FALSE END AS policy_covers_vehicle,
  c.policy_coverage_id,
  cov.coverage_type_code,
  cl.claim_lifecycle_id
FROM ${var:database}.claim c
LEFT JOIN ${var:database}.loss_event le ON c.loss_event_id = le.loss_event_id
LEFT JOIN ${var:database}.insurance_policy p ON c.policy_id = p.policy_id
LEFT JOIN ${var:database}.vehicle v ON c.insurable_object_id = v.insurable_object_id
LEFT JOIN ${var:database}.policy_insurable_object pio
  ON pio.policy_id = c.policy_id
 AND pio.insurable_object_id = c.insurable_object_id
 AND pio.expiration_date IS NULL
LEFT JOIN ${var:database}.policy_coverage pc ON c.policy_coverage_id = pc.policy_coverage_id
LEFT JOIN ${var:database}.coverage cov ON pc.coverage_id = cov.coverage_id
LEFT JOIN ${var:database}.claim_lifecycle cl ON cl.claim_id = c.claim_id
WHERE c.claim_id = '${var:claim_id}'
LIMIT 1;

-- result: roles
SELECT claim_party_role_id, party_id, role_type_code, is_current_assignment
FROM ${var:database}.claim_party_role
WHERE claim_id = '${var:claim_id}'
  AND is_current_assignment = TRUE;
