# Catalog Gatekeeper

An AI agent that audits and repairs a Shopify product catalog. Its store adapter speaks the Admin GraphQL API, version 2026-10. It proposes fixes, cites the store policy it relied on, passes every proposal through checks a program can verify, queues it for a person, and only then writes back.

Product page: https://utsgjohnsonsong.github.io/catalog-gatekeeper/. It walks one product through the whole flow.

Live demo: https://catalog-gatekeeper-demo.onrender.com. It sleeps when idle, so the first visit can take about a minute.

**Why it exists.** An LLM can write a good product description, and it can also invent a warranty or follow an instruction hidden in a supplier's text. The interesting problem is what stands between the model and the store. Here that is a narrow tool surface, 12 deterministic checks (11 of them can stop a fix), citations checked word for word, a person approving an exact diff, and a write-back that refuses to overwrite a merchant's newer edit.

**The demo runs with no account and no key, so two parts are simulated.** The store is a fixture, and the model is a template script, not an LLM. The eval data is synthetic. A real model has run once, on 40 synthetic products, through the eval rather than the demo. The live store adapter and the embedded mode, which opens the review screen inside a store's Shopify admin, are tested against mocked Shopify HTTP and real Postgres; neither has run against a real store yet. See the table below.

## Run it

```bash
docker compose up --build
```

Open http://127.0.0.1:8000 for the landing page, or http://127.0.0.1:8000/app/ for the review screen, and sign in with the filled-in demo token. Open a fix, read the diff and the checks, approve it, and watch it get written. "Edit as merchant" on the Products page sends a signed webhook, and any open fix for that product goes out of date.

```bash
python scripts/smoke.py
```

That drives the same flow over HTTP against the running stack.

The same demo runs on the internet for free at https://catalog-gatekeeper-demo.onrender.com: one container on Render's free plan and a free
Neon database, in a public mode with rate limits, a reviewer-only token and periodic resets. `docs/DEPLOY_DEMO.md`
has the setup and the latest check against the live service.

## What is real and what is not

| Part | State |
|---|---|
| FastAPI, Postgres, pgvector, job queue, write-back, webhooks | **Ran for real**, in Docker and in tests against real Postgres |
| Review UI (React on Shopify's Polaris web components) and landing page | **Ran for real** in a browser, at 375, 768 and 1280 widths, behind the real security headers. The landing page has light and dark; the review UI stays light, because Polaris documents no dark mode |
| MCP server | **Ran for real** over streamable HTTP and through an in-process client |
| Deterministic rules and citation checker | **Ran for real**, measured on synthetic data (`docs/EVAL.md`) |
| RAG | **Ran for real**, on 19 chunks of invented policy, with lexical hashed embeddings, not a semantic model |
| CI workflow | **Ran on GitHub.** On 2026-11-23 a manual run passed all seven jobs, gitleaks over the whole history included. Pushes that contain the root commit still fail `security`, because gitleaks cannot resolve its parent |
| Hosted LLM provider and judge | **Ran for real, once**, on the dev split: 120 agent calls and 40 judge calls to a hosted LLM |
| Agent behaviour, cost, latency | **Measured once on 40 synthetic products** (see Results). Repair quality has no automatic score. The policy search and citation path was never used by the real model |
| Free public demo (one container, Render and Neon) | **Online** at https://catalog-gatekeeper-demo.onrender.com. Run against it on 2026-11-24, `scripts/smoke_public.py` passed 12 of its 13 checks; the per-visitor rate limit did not trigger behind Render's proxy, output in `eval/reports/checks-2026-11-24/`. Before that, `docker-compose.public.yml` with the same script passed locally on 2026-11-03 and again on 2026-11-04 with the Polaris UI and the landing page; output in `eval/reports/checks-2026-11-04/` |
| Live Shopify adapter | **Written, not run.** Tested against a mocked GraphQL server only, token renewal included. It has never called a store |
| Embedded mode: App Bridge session tokens, token exchange for expiring offline tokens, the privacy webhooks | **Written, not run in a store.** Tested against mocked Shopify HTTP and real Postgres (`tests/integration/test_embedded_app.py`), including the refresh race between the API and the worker, with two token sources on separate connections, and a webhook forged from a session token. Steps in `docs/EMBEDDED.md` |
| Terraform for ECS and RDS | **Validated with `terraform validate`, never applied** |

`docs/DEMO_HONESTY.md` has the detail, `docs/REVIEW_2026-10-31.md` lists what review found and what stayed unfixed.

## How it works

```
sync -> audit rules -> findings -> agent loop (tools only) -> proposal -> 12 validators -> judge (can only raise risk)
                                                                                |
store <- write-back (hash check, read-back) <- approved <- person in the review UI
```

- **Rules block, the LLM judge only escalates.** A database constraint means a judge result can never be a block.
- **A citation is doc, chunk and verbatim quote**, from a passage retrieved in that same run. It proves the words exist. It does not prove the passage applies, so a person still checks that.
- **Approval binds exact content.** The UI sends back a hash of what it showed, and a trigger makes the proposal immutable.
- **Write-back is safe to retry.** Hash checks decide whether an earlier attempt landed. Shopify documents no precondition on `productUpdate`, so a window of about one round trip remains on a live store (`docs/adr/0004-content-hash-cas.md`).
- **A webhook is only a signal.** HMAC on raw bytes, deduped by id, then the store is refetched.
- **Untrusted text is fenced**, and tools are bound to one product. The fence is a hint. The structure is the defense.

Start with `docs/ARCHITECTURE.md`. Decisions and their costs are in `docs/adr/`, and on-call steps in `docs/RUNBOOK.md`.

## Results

Rules on synthetic data, held-out test split, first run (`docs/EVAL.md`):

- Audit rules: precision 0.960, recall 0.911 (358 of 393). **A13, the injection phrase list, caught only 10 of 30 unseen phrasings and fails its gate floor.** It is recorded as a known failure, not tuned away.
- Validators: precision 0.982, recall 0.979 (323 of 330). False blocks 2 of 102.
- Citation checker: rejected 57 of 57 invalid citations, and wrongly rejected 0 of 25 valid ones.
- Retrieval: recall@3 19 of 19 on questions worded like the policy, 2 of 8 on paraphrases. The second number is the cost of lexical embeddings.

These measure rules against their specification. They say nothing about an LLM.

One real-model run on a hosted LLM, 40 synthetic products on the dev split (`eval/reports/agent-dev-20261102T095137Z.json`):

- All 40 runs finished, and all 40 proposals stopped at human review. None was blocked, so this run did not test the validators against a bad proposal.
- The injected text was not followed in any of the 10 injection proposals, from reading them by hand. All 10 were flagged, but the agent had already read A13's finding, and A13 was tuned on this split. So this does not show it can spot wording A13 misses.
- The judge raised 2 concerns, both about replacing "Coming soon": the new text no longer says the item is unavailable. A03 treats that wording as a placeholder, and the judge's point is a real gap in that rule.
- No citations. The model never called policy search, because no proposal touched a policy topic.
- Cost 1.02 USD in total, about 0.025 per product. Latency p50 12.8 s, p95 20.3 s.

The judge, validator and retrieval figures come from a database extract saved beside the report, because that report predates those metrics. This is one small run, not a repair-quality score, so no accuracy is claimed.

## Tests

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
docker compose up -d db
pytest tests
python -m gatekeeper_eval all --split dev
python -m gatekeeper_eval all --split test
cd frontend && npm ci && npm test
```

On Windows use `.venv\Scripts\activate`. Tests use real Postgres, not mocks. Coverage is reported with `pytest tests --cov`.

## Limits

- One store per deployment. Embedded mode follows Shopify's managed installation, session tokens, expiring offline tokens and the privacy webhooks, but the app is not in the App Store and has no billing (`docs/adr/0013-embedded-one-store.md`).
- Reviewer tokens never expire. Rate limits exist only in the public demo mode, in one process's memory. Put a WAF or a CIDR rule in front before real use.
- Rules are English word lists, so they miss paraphrases and can false-block plain English.
- Only the first 25 variants and 10 media of a product are read from a live store.
- With `SHOPIFY_CLIENT_ID` and `SHOPIFY_CLIENT_SECRET`, the live adapter renews its 24-hour token itself. That is tested against mocked HTTP only.
- The eval data is synthetic. Its author also wrote the rules.
- The review UI loads Polaris from `cdn.shopify.com` at run time, so it needs that CDN to be reachable, and Shopify's script runs with the reviewer's token in reach. Without the CDN the page says it could not load. The `/app` pages allow only Shopify's `/shopifycloud/` and `/static/fonts/` paths on that host, not the merchant uploads it also serves, and inline styles, because Polaris writes its own `<style>` blocks. No inline script is allowed.

## Steps that need an account or a key

A real-model eval run, an install in a development store, the free demo and an AWS deployment each need an account, a key or spending approval. The steps are in [docs/OWNER_STEPS.md](docs/OWNER_STEPS.md).
