DELETE FROM ${var:database}.agent_run_audit WHERE run_id = '{run_id}';

DELETE FROM ${var:database}.agent_run_evidence WHERE run_id = '{run_id}';
