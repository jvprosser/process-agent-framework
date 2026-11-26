-- Additive fact-assertion tables for an already-loaded Iceberg lake.
-- Run on the Trino coordinator (catalog iceberg). asserted_at stays VARCHAR
-- because the writer inserts the sidecar's UTC ISO-8601 text.

CREATE TABLE IF NOT EXISTS iceberg.car_insurance_claims.claim_fact_assertions (
  claim_id BIGINT,
  write_run_id VARCHAR,
  http_run_id VARCHAR,
  lane VARCHAR,
  agent_role VARCHAR,
  field_name VARCHAR,
  field_value VARCHAR,
  asserted_at VARCHAR
);

CREATE TABLE IF NOT EXISTS iceberg.underwriting.underwriting_fact_assertions (
  submission_id BIGINT,
  write_run_id VARCHAR,
  http_run_id VARCHAR,
  lane VARCHAR,
  agent_role VARCHAR,
  field_name VARCHAR,
  field_value VARCHAR,
  asserted_at VARCHAR
);
