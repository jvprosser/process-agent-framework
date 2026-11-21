-- result: refresh_loss_driver
REFRESH ${var:database}.loss_driver;

-- result: refresh_claim
REFRESH ${var:database}.claim;

-- result: refresh_police_report
REFRESH ${var:database}.police_report;

-- result: refresh_fault_determination
REFRESH ${var:database}.fault_determination;

-- result: refresh_claim_police_intake
REFRESH ${var:database}.claim_police_intake;

-- result: refresh_claim_outbound_message
REFRESH ${var:database}.claim_outbound_message;

-- result: signals
WITH
sub AS (
  SELECT COUNT(*) AS cnt,
         MIN(subrogation_case_id) AS subrogation_case_id,
         MIN(subrogation_status_code) AS subrogation_status_code
  FROM ${var:database}.subrogation_case WHERE claim_id = '${var:claim_id}'
),
lit AS (
  SELECT COUNT(*) AS cnt,
         MIN(litigation_case_id) AS litigation_case_id,
         MIN(docket_number) AS docket_number,
         MIN(defense_counsel_party_id) AS defense_counsel_party_id,
         MIN(plaintiff_counsel_party_id) AS plaintiff_counsel_party_id,
         MIN(served_date) AS served_date,
         MIN(filed_date) AS filed_date,
         MIN(closed_date) AS closed_date,
         MIN(litigation_status_code) AS litigation_status_code
  FROM ${var:database}.litigation_case WHERE claim_id = '${var:claim_id}'
),
inj AS (
  SELECT COUNT(*) AS cnt FROM ${var:database}.claim_injury WHERE claim_id = '${var:claim_id}'
),
pr AS (
  SELECT COUNT(*) AS cnt, MIN(police_report_id) AS police_report_id
  FROM ${var:database}.police_report WHERE claim_id = '${var:claim_id}'
),
intake AS (
  SELECT COUNT(*) AS cnt,
         MIN(incident_report_number) AS incident_report_number
  FROM ${var:database}.claim_police_intake
  WHERE claim_id = '${var:claim_id}'
    AND incident_report_number IS NOT NULL
    AND TRIM(incident_report_number) <> ''
),
sms AS (
  SELECT COUNT(*) AS cnt
  FROM ${var:database}.claim_outbound_message
  WHERE claim_id = '${var:claim_id}'
    AND purpose_code = 'COLLECT_INCIDENT_REPORT_NUMBER'
),
fd AS (
  SELECT COUNT(*) AS cnt, MIN(fault_determination_id) AS fault_determination_id
  FROM ${var:database}.fault_determination WHERE claim_id = '${var:claim_id}'
),
ofr AS (
  SELECT COUNT(*) AS cnt,
         SUM(CASE WHEN offer_status_code = 'EXTENDED' THEN 1 ELSE 0 END) AS extended_cnt,
         SUM(CASE WHEN offer_status_code = 'ACCEPTED' THEN 1 ELSE 0 END) AS accepted_cnt
  FROM ${var:database}.claim_offer WHERE claim_id = '${var:claim_id}'
),
pay AS (
  SELECT COUNT(*) AS cnt FROM ${var:database}.claim_payment WHERE claim_id = '${var:claim_id}'
),
rec AS (
  SELECT COUNT(*) AS cnt FROM ${var:database}.claim_recovery WHERE claim_id = '${var:claim_id}'
),
res AS (
  SELECT COUNT(*) AS cnt FROM ${var:database}.claim_reserve
  WHERE claim_id = '${var:claim_id}' AND is_current = TRUE
),
fa AS (
  SELECT COUNT(*) AS cnt,
         MIN(fraud_assessment_id) AS fraud_assessment_id,
         MIN(outcome_code) AS fraud_outcome_code
  FROM ${var:database}.fraud_assessment
  WHERE claim_id = '${var:claim_id}' AND outcome_code IN ('SUSPECTED', 'PENDING')
),
doc AS (
  SELECT COUNT(*) AS cnt FROM ${var:database}.claim_document WHERE claim_id = '${var:claim_id}'
)
SELECT
  (sub.cnt > 0) AS has_subrogation_case,
  sub.subrogation_case_id,
  sub.subrogation_status_code,
  (lit.cnt > 0) AS has_litigation_case,
  lit.litigation_case_id,
  lit.docket_number,
  lit.defense_counsel_party_id,
  lit.plaintiff_counsel_party_id,
  lit.served_date,
  lit.filed_date,
  lit.closed_date,
  lit.litigation_status_code,
  (inj.cnt > 0) AS has_injury,
  (pr.cnt > 0) AS has_police_report,
  pr.police_report_id,
  (fd.cnt > 0) AS has_fault_determination,
  fd.fault_determination_id,
  (intake.cnt > 0) AS has_incident_report_number,
  intake.incident_report_number,
  (sms.cnt > 0) AS has_collect_incident_sms,
  (ofr.cnt > 0) AS has_offer,
  (COALESCE(ofr.extended_cnt, 0) > 0) AS has_unresolved_offer,
  (COALESCE(ofr.accepted_cnt, 0) > 0) AS has_accepted_offer,
  (pay.cnt > 0) AS has_loss_payment,
  (rec.cnt > 0) AS has_recovery,
  (res.cnt > 0) AS has_current_reserve,
  (fa.cnt > 0) AS has_siu_suspected,
  fa.fraud_assessment_id,
  fa.fraud_outcome_code,
  (doc.cnt > 0) AS has_document
FROM sub
CROSS JOIN lit
CROSS JOIN inj
CROSS JOIN pr
CROSS JOIN fd
CROSS JOIN intake
CROSS JOIN sms
CROSS JOIN ofr
CROSS JOIN pay
CROSS JOIN rec
CROSS JOIN res
CROSS JOIN fa
CROSS JOIN doc;

-- result: injury_ids
SELECT claim_injury_id FROM ${var:database}.claim_injury WHERE claim_id = '${var:claim_id}';

-- result: insured_operators
SELECT
  ld.driver_id,
  ld.was_cited_indicator,
  ld.impairment_suspected_indicator,
  d.license_status_code,
  CASE WHEN pd.driver_id IS NOT NULL THEN TRUE ELSE FALSE END AS on_policy,
  pd.is_excluded_driver
FROM ${var:database}.loss_driver ld
LEFT JOIN ${var:database}.driver d ON d.driver_id = ld.driver_id
INNER JOIN ${var:database}.claim c ON c.claim_id = ld.claim_id
LEFT JOIN ${var:database}.policy_driver pd
  ON pd.policy_id = c.policy_id
 AND pd.driver_id = ld.driver_id
 AND pd.expiration_date IS NULL
WHERE ld.claim_id = '${var:claim_id}'
  AND ld.driver_role_code = 'INSURED_OPERATOR';

-- result: offers
SELECT claim_offer_id, offer_status_code
FROM ${var:database}.claim_offer
WHERE claim_id = '${var:claim_id}';

-- result: payment_ids
SELECT claim_payment_id FROM ${var:database}.claim_payment WHERE claim_id = '${var:claim_id}';

-- result: recovery_ids
SELECT claim_recovery_id FROM ${var:database}.claim_recovery WHERE claim_id = '${var:claim_id}';
