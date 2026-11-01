DROP VIEW IF EXISTS v_policy_chunks, v_audit_log, v_validation, v_proposals, v_findings, v_products;
DROP TABLE IF EXISTS heartbeats, jobs, webhook_events, writebacks, reviews, validation_results, audit_log,
  proposal_transitions, proposals, run_retrievals, llm_calls, agent_runs, embedder_state, policy_chunks,
  policy_docs, findings, products, shops CASCADE;
DROP FUNCTION IF EXISTS proposals_guard();
DROP FUNCTION IF EXISTS raise_immutable();
DROP TYPE IF EXISTS job_status, run_status, finding_status, rule_severity, audit_severity, proposal_status;
