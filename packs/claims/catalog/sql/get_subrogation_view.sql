SELECT subrogation_status_code, demand_amount, recovered_amount, currency_code,
       opened_date, closed_date, statute_limitations_date, created_at
FROM ${var:database}.subrogation_case
WHERE claim_id = '${var:claim_id}'
