SELECT litigation_status_code, docket_number, venue_name,
       venue_country_subdivision_code, filed_date, served_date, closed_date,
       demand_amount, currency_code, created_at
FROM ${var:database}.litigation_case
WHERE claim_id = ${var:claim_id}
