WITH
inj AS (
  SELECT COUNT(*) AS cnt FROM ${var:database}.fnol_injury WHERE fnol_id = ${var:fnol_id}
),
tpv AS (
  SELECT COUNT(*) AS cnt FROM ${var:database}.fnol_third_party_vehicle WHERE fnol_id = ${var:fnol_id}
),
unl AS (
  SELECT COUNT(*) AS cnt
  FROM ${var:database}.fnol f
  INNER JOIN ${var:database}.loss_driver ld
    ON ld.loss_event_id = f.loss_event_id
   AND ld.driver_role_code = 'INSURED_OPERATOR'
  LEFT JOIN ${var:database}.policy_driver pd
    ON pd.policy_id = f.policy_id
   AND pd.driver_id = ld.driver_id
   AND pd.expiration_date IS NULL
  WHERE f.fnol_id = ${var:fnol_id}
    AND pd.driver_id IS NULL
)
SELECT
  (inj.cnt > 0) AS has_injury,
  (tpv.cnt > 0) AS has_third_party_vehicle,
  (unl.cnt > 0) AS has_unlisted_insured_operator
FROM inj
CROSS JOIN tpv
CROSS JOIN unl
