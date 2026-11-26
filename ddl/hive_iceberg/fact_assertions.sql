-- Additive fact-assertion tables for an already-loaded lake.
-- Fresh Trino coordinator: ddl/trino/fact_assertions.sql.
-- asserted_at is STRING so the ISO-8601 sidecar text inserts without a cast.

CREATE TABLE IF NOT EXISTS car_insurance_claims.claim_fact_assertions (
  claim_id                  BIGINT    COMMENT 'FK -> claim.claim_id.',
  write_run_id              STRING    COMMENT 'Lake run id, run-{entity_id}.',
  http_run_id               STRING    COMMENT 'Chat request UUID.',
  lane                      STRING    COMMENT 'Lane that asserted the fact.',
  agent_role                STRING    COMMENT 'Specialist role that asserted the fact.',
  field_name                STRING    COMMENT 'Ontology field name.',
  field_value               STRING    COMMENT 'Asserted value as text.',
  asserted_at               STRING    COMMENT 'UTC ISO-8601 timestamp from the sidecar.'
)
COMMENT 'Append-only chat fact assertions. Latest row per field_name overlays the session graph.'
STORED BY ICEBERG
TBLPROPERTIES ('format-version' = '2');

CREATE TABLE IF NOT EXISTS underwriting.underwriting_fact_assertions (
  submission_id             BIGINT    COMMENT 'FK -> submissions.submission_id. Bound from ${var:claim_id}.',
  write_run_id              STRING    COMMENT 'Lake run id, run-{entity_id}.',
  http_run_id               STRING    COMMENT 'Chat request UUID.',
  lane                      STRING    COMMENT 'Lane that asserted the fact.',
  agent_role                STRING    COMMENT 'Specialist role that asserted the fact.',
  field_name                STRING    COMMENT 'Ontology field name.',
  field_value               STRING    COMMENT 'Asserted value as text.',
  asserted_at               STRING    COMMENT 'UTC ISO-8601 timestamp from the sidecar.'
)
COMMENT 'Append-only chat fact assertions. Latest row per field_name overlays the session graph.'
STORED BY ICEBERG
TBLPROPERTIES ('format-version' = '2');

INVALIDATE METADATA car_insurance_claims.claim_fact_assertions;
INVALIDATE METADATA underwriting.underwriting_fact_assertions;
