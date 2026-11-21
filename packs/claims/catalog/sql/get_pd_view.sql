-- result: police_reports
SELECT report_number, agency_name, report_datetime, report_date,
       citation_issued_indicator, narrative_summary, created_at
FROM ${var:database}.police_report
WHERE claim_id = '${var:claim_id}';

-- result: fault_determinations
SELECT insured_fault_percent, adverse_fault_percent, fault_basis_code,
       determination_status_code, determination_datetime, notes, created_at
FROM ${var:database}.fault_determination
WHERE claim_id = '${var:claim_id}';

-- result: incident_report
SELECT incident_report_number
FROM ${var:database}.claim_police_intake
WHERE claim_id = '${var:claim_id}'
  AND incident_report_number IS NOT NULL
  AND TRIM(incident_report_number) <> ''
ORDER BY collected_at DESC
LIMIT 1;

-- result: last_sms
SELECT to_phone, body_text, created_at
FROM ${var:database}.claim_outbound_message
WHERE claim_id = '${var:claim_id}'
  AND purpose_code = 'COLLECT_INCIDENT_REPORT_NUMBER'
ORDER BY created_at DESC
LIMIT 1;
