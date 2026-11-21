SELECT injury_severity_code, body_region_code, injury_description,
       treatment_start_date, treatment_end_date, ambulance_used_indicator,
       hospitalization_indicator, created_at
FROM ${var:database}.claim_injury
WHERE claim_id = '${var:claim_id}'
