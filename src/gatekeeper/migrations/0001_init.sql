CREATE EXTENSION IF NOT EXISTS vector;

CREATE TYPE proposal_status AS ENUM ('pending_review','blocked','approved','applying','applied','rejected','stale','failed');
CREATE TYPE audit_severity  AS ENUM ('info','low','medium','high','critical');
CREATE TYPE rule_severity   AS ENUM ('block','warn');
CREATE TYPE finding_status  AS ENUM ('open','resolved');
CREATE TYPE run_status      AS ENUM ('running','finished','failed','budget_exceeded','max_iterations','no_finish');
CREATE TYPE job_status      AS ENUM ('queued','running','done','dead');

CREATE TABLE shops (
  id bigserial PRIMARY KEY,
  domain text NOT NULL UNIQUE,
  writeback_enabled boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE products (
  id bigserial PRIMARY KEY,
  shop_id bigint NOT NULL REFERENCES shops,
  gid text NOT NULL,
  handle text NOT NULL,
  title text NOT NULL,
  vendor text NOT NULL DEFAULT '',
  product_type text NOT NULL DEFAULT '',
  status text NOT NULL,
  tags text[] NOT NULL DEFAULT '{}',
  description_html text NOT NULL DEFAULT '',
  seo_title text NOT NULL DEFAULT '',
  seo_description text NOT NULL DEFAULT '',
  variants jsonb NOT NULL DEFAULT '[]',
  images jsonb NOT NULL DEFAULT '[]',
  metafields jsonb NOT NULL DEFAULT '{}',
  shopify_updated_at timestamptz NOT NULL,
  content_hash text NOT NULL,
  synced_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz,
  UNIQUE (shop_id, gid)
);

CREATE TABLE findings (
  id bigserial PRIMARY KEY,
  shop_id bigint NOT NULL REFERENCES shops,
  product_id bigint NOT NULL REFERENCES products,
  rule_id text NOT NULL,
  rule_version int NOT NULL,
  severity audit_severity NOT NULL,
  fixable boolean NOT NULL,
  subject text NOT NULL DEFAULT '',
  fields text[] NOT NULL,
  evidence jsonb NOT NULL,
  product_hash text NOT NULL,
  status finding_status NOT NULL DEFAULT 'open',
  first_seen timestamptz NOT NULL DEFAULT now(),
  last_seen timestamptz NOT NULL DEFAULT now(),
  resolved_at timestamptz,
  UNIQUE (product_id, rule_id, subject)
);

CREATE TABLE policy_docs (
  id text PRIMARY KEY,
  title text NOT NULL,
  body text NOT NULL,
  doc_hash text NOT NULL
);

CREATE TABLE policy_chunks (
  id text PRIMARY KEY,
  doc_id text NOT NULL REFERENCES policy_docs ON DELETE CASCADE,
  ordinal int NOT NULL,
  text text NOT NULL,
  embedder_id text NOT NULL,
  embedding vector NOT NULL,
  retired_at timestamptz, -- a removed passage stays for the audit trail but can no longer be retrieved or cited
  UNIQUE (doc_id, ordinal),
  CHECK (id = doc_id || '#' || lpad(ordinal::text, 3, '0'))
);

CREATE TABLE embedder_state (
  embedder_id text PRIMARY KEY,
  state jsonb NOT NULL
);

CREATE TABLE agent_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  shop_id bigint NOT NULL REFERENCES shops,
  product_id bigint NOT NULL REFERENCES products,
  provider text NOT NULL,
  model text NOT NULL,
  mode text NOT NULL CHECK (mode IN ('live','demo','test')),
  prompt_version text NOT NULL,
  base_content_hash text NOT NULL,
  status run_status NOT NULL DEFAULT 'running',
  iterations int NOT NULL DEFAULT 0,
  tool_calls int NOT NULL DEFAULT 0,
  input_tokens int NOT NULL DEFAULT 0,
  output_tokens int NOT NULL DEFAULT 0,
  cost_usd numeric(10,6) NOT NULL DEFAULT 0,
  flags text[] NOT NULL DEFAULT '{}',
  outcome text,
  error text,
  transcript jsonb NOT NULL DEFAULT '[]',
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);

CREATE TABLE llm_calls (
  id bigserial PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES agent_runs,
  iteration int NOT NULL,
  role text NOT NULL CHECK (role IN ('agent','judge')),
  model text NOT NULL,
  stop_reason text,
  input_tokens int,
  output_tokens int,
  cache_read_tokens int,
  cost_usd numeric(10,6),
  latency_ms int
);

CREATE TABLE run_retrievals (
  run_id uuid NOT NULL REFERENCES agent_runs,
  chunk_id text NOT NULL REFERENCES policy_chunks,
  query text NOT NULL,
  rank int NOT NULL,
  score real NOT NULL,
  PRIMARY KEY (run_id, chunk_id)
);

CREATE TABLE proposals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  shop_id bigint NOT NULL REFERENCES shops,
  product_id bigint NOT NULL REFERENCES products,
  run_id uuid REFERENCES agent_runs,
  base_content_hash text NOT NULL,
  proposed jsonb NOT NULL,
  proposed_hash text NOT NULL,
  rationale text NOT NULL DEFAULT '',
  citations jsonb NOT NULL DEFAULT '[]',
  status proposal_status NOT NULL,
  risk_score int NOT NULL DEFAULT 0 CHECK (risk_score BETWEEN 0 AND 100),
  risk_reasons jsonb NOT NULL DEFAULT '[]',
  judge_status text NOT NULL DEFAULT 'disabled' CHECK (judge_status IN ('disabled','skipped_blocked','ok','unavailable')),
  judge_discarded int NOT NULL DEFAULT 0,
  approved_hash text,
  version int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  decided_at timestamptz,
  applied_at timestamptz,
  CHECK (status NOT IN ('approved','applying','applied') OR approved_hash IS NOT NULL),
  CHECK (status NOT IN ('pending_review','blocked') OR approved_hash IS NULL)
);

-- A decided fix is never re-proposed for the same base. Blocked attempts do not count, so the same text can be
-- resubmitted with a corrected citation.
CREATE UNIQUE INDEX one_decided_fix_per_base ON proposals (product_id, base_content_hash, proposed_hash)
  WHERE status <> 'blocked';

-- 'blocked' is closed, so the agent may retry after a blocked attempt
CREATE UNIQUE INDEX one_open_proposal_per_product ON proposals (product_id)
  WHERE status IN ('pending_review','approved','applying');

CREATE TABLE proposal_transitions (
  from_status proposal_status,
  to_status proposal_status,
  PRIMARY KEY (from_status, to_status)
);
INSERT INTO proposal_transitions VALUES
  ('pending_review','approved'),('pending_review','rejected'),('pending_review','stale'),
  ('approved','applying'),('approved','rejected'),('approved','stale'),
  ('applying','applied'),('applying','stale'),('applying','failed'),
  ('applying','approved');

CREATE TABLE audit_log (
  id bigserial PRIMARY KEY,
  at timestamptz NOT NULL DEFAULT now(),
  actor text NOT NULL,
  action text NOT NULL,
  entity_type text NOT NULL,
  entity_id text NOT NULL,
  shop_id bigint,
  detail jsonb NOT NULL DEFAULT '{}'
);

CREATE FUNCTION raise_immutable() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'append-only table';
END
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_append_only BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION raise_immutable();

CREATE FUNCTION proposals_guard() RETURNS trigger AS $$
BEGIN
  IF NEW.proposed IS DISTINCT FROM OLD.proposed
     OR NEW.proposed_hash IS DISTINCT FROM OLD.proposed_hash
     OR NEW.base_content_hash IS DISTINCT FROM OLD.base_content_hash
     OR NEW.product_id IS DISTINCT FROM OLD.product_id
     OR NEW.shop_id IS DISTINCT FROM OLD.shop_id
     OR (OLD.approved_hash IS NOT NULL AND NEW.approved_hash IS DISTINCT FROM OLD.approved_hash)
  THEN
    RAISE EXCEPTION 'proposal content and approved_hash are immutable';
  END IF;
  IF NEW.status IS DISTINCT FROM OLD.status THEN
    IF NOT EXISTS (SELECT 1 FROM proposal_transitions WHERE from_status = OLD.status AND to_status = NEW.status) THEN
      RAISE EXCEPTION 'illegal proposal transition % -> %', OLD.status, NEW.status;
    END IF;
    INSERT INTO audit_log(actor, action, entity_type, entity_id, shop_id, detail)
    VALUES (coalesce(nullif(current_setting('gatekeeper.actor', true), ''), 'unknown'),
            'proposal.' || NEW.status, 'proposal', NEW.id::text, NEW.shop_id,
            jsonb_build_object('from', OLD.status, 'to', NEW.status));
  END IF;
  NEW.updated_at := now();
  NEW.version := OLD.version + 1;
  RETURN NEW;
END
$$ LANGUAGE plpgsql;

CREATE TRIGGER proposals_guard BEFORE UPDATE ON proposals
  FOR EACH ROW EXECUTE FUNCTION proposals_guard();

CREATE TABLE validation_results (
  id bigserial PRIMARY KEY,
  proposal_id uuid NOT NULL REFERENCES proposals,
  rule_id text NOT NULL,
  rule_version int NOT NULL,
  kind text NOT NULL CHECK (kind IN ('validator','judge')),
  severity rule_severity NOT NULL,
  passed boolean NOT NULL,
  evidence jsonb NOT NULL,
  UNIQUE (proposal_id, rule_id),
  CHECK (kind <> 'judge' OR severity = 'warn') -- the judge can never insert a block
);

CREATE TABLE reviews (
  id bigserial PRIMARY KEY,
  proposal_id uuid NOT NULL UNIQUE REFERENCES proposals,
  reviewer text NOT NULL,
  decision text NOT NULL CHECK (decision IN ('approve','reject')),
  note text NOT NULL DEFAULT '',
  approved_hash text,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK ((decision = 'approve') = (approved_hash IS NOT NULL))
);

CREATE TABLE writebacks (
  id bigserial PRIMARY KEY,
  proposal_id uuid NOT NULL UNIQUE REFERENCES proposals,
  idempotency_key text NOT NULL UNIQUE, -- internal; Shopify documents no idempotency for productUpdate
  status text NOT NULL CHECK (status IN ('claimed','sent','verified','failed','stale')),
  attempts int NOT NULL DEFAULT 1,
  before_snapshot jsonb,
  expected_after_hash text,
  request jsonb,
  response jsonb,
  user_errors jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE webhook_events (
  id bigserial PRIMARY KEY,
  webhook_id text NOT NULL UNIQUE,
  event_id text,
  shop_domain text NOT NULL,
  topic text NOT NULL,
  triggered_at timestamptz,
  payload jsonb NOT NULL,
  status text NOT NULL DEFAULT 'received' CHECK (status IN ('received','processed','ignored','failed')),
  error text,
  received_at timestamptz NOT NULL DEFAULT now(),
  processed_at timestamptz
);

CREATE TABLE jobs (
  id bigserial PRIMARY KEY,
  kind text NOT NULL,
  payload jsonb NOT NULL,
  dedupe_key text UNIQUE,
  status job_status NOT NULL DEFAULT 'queued',
  run_after timestamptz NOT NULL DEFAULT now(),
  attempts int NOT NULL DEFAULT 0,
  max_attempts int NOT NULL DEFAULT 5,
  locked_at timestamptz,
  locked_by text,
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX jobs_runnable ON jobs (run_after) WHERE status = 'queued';

CREATE TABLE heartbeats (
  name text PRIMARY KEY,
  at timestamptz NOT NULL
);

-- Read models for the MCP server. No secrets, no embeddings, no transcripts.
CREATE VIEW v_products AS
  SELECT p.id, p.gid, p.handle, p.title, p.vendor, p.product_type, p.status, p.tags,
         p.description_html, p.seo_title, p.seo_description, p.variants, p.images,
         p.content_hash, p.shopify_updated_at
  FROM products p WHERE p.deleted_at IS NULL;
CREATE VIEW v_findings AS
  SELECT f.id, f.product_id, f.rule_id, f.severity, f.fixable, f.subject, f.fields, f.evidence, f.status, f.last_seen
  FROM findings f;
CREATE VIEW v_proposals AS
  SELECT pr.id, pr.product_id, pr.status, pr.risk_score, pr.risk_reasons, pr.proposed, pr.rationale,
         pr.citations, pr.base_content_hash, pr.created_at, ar.provider, ar.model, ar.mode
  FROM proposals pr LEFT JOIN agent_runs ar ON ar.id = pr.run_id;
CREATE VIEW v_validation AS
  SELECT proposal_id, rule_id, kind, severity, passed, evidence FROM validation_results;
CREATE VIEW v_audit_log AS
  SELECT id, at, actor, action, entity_type, entity_id, detail FROM audit_log;
CREATE VIEW v_policy_chunks AS
  SELECT c.id, c.doc_id, c.ordinal, c.text, d.title AS doc_title FROM policy_chunks c JOIN policy_docs d ON d.id = c.doc_id
  WHERE c.retired_at IS NULL;
