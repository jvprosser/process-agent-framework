-- result: fnol
SELECT fnol_id, fnol_number, triage_status_code, claim_id, policy_id,
       insurable_object_id, loss_event_id, estimated_loss_amount,
       report_timestamp, intake_purpose_code
FROM ${var:database}.fnol
WHERE fnol_id = ${var:fnol_id}
LIMIT 1;

-- result: coverage
SELECT pc.policy_coverage_id, pc.deductible_amount, cov.coverage_type_code
FROM ${var:database}.fnol f
INNER JOIN ${var:database}.loss_event le ON le.loss_event_id = f.loss_event_id
INNER JOIN ${var:database}.policy_coverage pc
  ON pc.policy_id = f.policy_id
 AND pc.is_active = TRUE
 AND pc.expiration_date IS NULL
INNER JOIN ${var:database}.coverage cov ON cov.coverage_id = pc.coverage_id
 AND (
   (UPPER(COALESCE(le.loss_cause_code, '')) = 'COLLISION'
    AND cov.coverage_type_code = 'COLLISION')
   OR (UPPER(COALESCE(le.loss_cause_code, '')) <> 'COLLISION'
    AND cov.coverage_type_code = 'COMPREHENSIVE')
 )
WHERE f.fnol_id = ${var:fnol_id}
LIMIT 1;

-- result: claim
INSERT INTO ${var:database}.claim (
  claim_id, claim_number, loss_event_id, policy_id, insurable_object_id,
  policy_coverage_id, fnol_report_datetime, claim_status_code,
  fraudulent_claim_indicator, litigation_indicator,
  subrogation_indicator, total_loss_indicator, created_at
) VALUES (
  {claim_id},
  '{claim_number}',
  {loss_event_id},
  {policy_id},
  {insurable_object_id},
  {policy_coverage_id},
  CURRENT_TIMESTAMP,
  'OPEN',
  FALSE,
  FALSE,
  FALSE,
  FALSE,
  CURRENT_TIMESTAMP
);

-- result: policyholder
SELECT party_id
FROM ${var:database}.policy_party_role
WHERE policy_id = {policy_id}
  AND role_type_code = 'POLICYHOLDER'
  AND expiration_date IS NULL
LIMIT 1;

-- result: insured_role
INSERT INTO ${var:database}.claim_party_role (
  claim_party_role_id, claim_id, party_id, role_type_code,
  assigned_at, unassigned_at, is_current_assignment, created_at
) VALUES (
  {insured_role_id},
  {claim_id},
  {party_id},
  'INSURED',
  CURRENT_TIMESTAMP,
  NULL,
  TRUE,
  CURRENT_TIMESTAMP
);

-- result: adjuster_role
INSERT INTO ${var:database}.claim_party_role (
  claim_party_role_id, claim_id, party_id, role_type_code,
  assigned_at, unassigned_at, is_current_assignment, created_at
) VALUES (
  {adjuster_role_id},
  {claim_id},
  4,
  'ADJUSTER',
  CURRENT_TIMESTAMP,
  NULL,
  TRUE,
  CURRENT_TIMESTAMP
);

-- result: reserve
INSERT INTO ${var:database}.claim_reserve (
  claim_reserve_id, claim_id, reserve_status_code, currency_code,
  initial_loss_reserve_amount, revised_reserve_total_amount,
  is_current, last_updated_at, created_at
) VALUES (
  {claim_reserve_id},
  {claim_id},
  'OPEN',
  'USD',
  {amount},
  {amount},
  TRUE,
  CURRENT_TIMESTAMP,
  CURRENT_TIMESTAMP
);

-- result: stamp
UPDATE ${var:database}.fnol
SET triage_status_code = 'CONVERTED', claim_id = {claim_id}
WHERE fnol_id = ${var:fnol_id}
  AND UPPER(COALESCE(triage_status_code, '')) IN ('OPEN', 'ADJUSTER_REVIEW');

-- result: audit
INSERT INTO ${var:database}.agent_run_audit (
  run_id, event_ts, claim_id, event_type, next_step, agent_role, lane,
  needs_llm, terminal, reason_probe_ids, payload_json
) VALUES (
  '{run_id}',
  CURRENT_TIMESTAMP,
  ${var:claim_id},
  '{event_type}',
  '{next_step}',
  '{agent_role}',
  '{lane}',
  '{needs_llm}',
  '{terminal}',
  '{reason_probe_ids}',
  '{payload_json}'
);
