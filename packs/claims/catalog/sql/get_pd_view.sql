SELECT report_number, agency_name, report_datetime, report_date,
       citation_issued_indicator, narrative_summary, created_at
FROM ${database}.police_report
WHERE claim_id = ${claim_id}
