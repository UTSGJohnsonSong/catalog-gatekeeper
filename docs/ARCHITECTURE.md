# Architecture

Catalog Gatekeeper audits a product catalog, lets an agent propose fixes, passes every proposal through checks a program can verify, queues it for a person, and only then writes back.

```
              webhook (signal only)                 agent loop (one product per run)
Shopify store ---------------------> FastAPI ---> jobs ---> LLMProvider (hosted LLM | template | scripted)
    ^    |                             |                      |  tools: get_product, list_findings,
    |    | ShopifyPort                 |                      |         search_policy, propose_fix, finish
    |    v (fixture or live)           v                      v
    |  sync --> products --> audit --> findings          proposals --> 12 validators (block or warn)
    |                                                         |              then judge (escalate only)
    |                                                         v
    +---- write-back (hash checks, read-back) <-- approved <-- review UI (React, Polaris): diff, evidence, approve
```

One image runs three processes: `api` (FastAPI plus the two built pages: the landing page and the review UI), `worker` (Postgres job queue), and `mcp` (read-only tools). There is no Redis and no Celery. Migrations and setup run as a one-off `migrate` step.

## Proposal states

```
INSERT -> blocked (terminal) | pending_review
pending_review -> approved | rejected | stale
approved       -> applying | rejected | stale
applying       -> applied | stale | failed | approved (kill switch release)
```

A database trigger enforces this table, makes proposal content immutable, and writes one audit row per transition. The audit log is append-only, by trigger and by grant.

## Where things live

| Area | Path |
|---|---|
| Hashes and product model | `src/gatekeeper/domain/` |
| Audit rules and validators | `src/gatekeeper/rules/` |
| Agent loop, tools, budgets, fencing | `src/gatekeeper/agent/` |
| Provider port and adapters, judge | `src/gatekeeper/llm/` |
| Chunking, embeddings, pgvector search, citation check | `src/gatekeeper/rag/` |
| Sync, proposals, review, write-back, webhooks | `src/gatekeeper/services/` |
| Shopify port, fixture and live adapters, HMAC | `src/gatekeeper/shopify/` |
| Job queue and worker | `src/gatekeeper/jobs.py`, `worker.py` |
| HTTP API and auth | `src/gatekeeper/api/` |
| Schema | `src/gatekeeper/migrations/` |
| MCP server | `src/gatekeeper_mcp/` |
| Eval generator, runners, gates | `src/gatekeeper_eval/`, `eval/` |
| Landing page and review UI | `frontend/`: `index.html` for `/`, `app/index.html` for `/app` |
| AWS | `infra/terraform/` |

Decisions and their costs are in `docs/adr/`. What is simulated is in `docs/DEMO_HONESTY.md`. On-call steps are in `docs/RUNBOOK.md`.
