# Catalog Gatekeeper

An AI agent that audits and repairs a Shopify-style product catalog. It proposes fixes, cites the store policy it relied on, passes every proposal through checks a program can verify, queues it for a person, and only then writes back.

**Why it exists.** An LLM can write a good product description, and it can also invent a warranty or follow an instruction hidden in a supplier's text. The interesting problem is what stands between the model and the store. Here that is a narrow tool surface, 12 deterministic checks, citations checked word for word, a person approving an exact diff, and a write-back that refuses to overwrite a merchant's newer edit.

**The demo runs with no account and no key, so two parts are simulated.** The store is a fixture, and the model is a template script, not an LLM. The eval data is synthetic. A real model has run once, on 40 synthetic products, through the eval rather than the demo. See the table below.

## Run it

```bash
docker compose up --build
```

Open http://127.0.0.1:8000 for the landing page, or http://127.0.0.1:8000/app/ for the review screen, and sign in with the filled-in demo token. Open a fix, read the diff and the checks, approve it, and watch it get written. "Edit as merchant" on the Products page sends a signed webhook, and any open fix for that product goes out of date.

```bash
python scripts/smoke.py
```

That drives the same flow over HTTP against the running stack.

To put the same demo on the internet for free, see `docs/DEPLOY_DEMO.md`: one container on Render's free plan and a
free Neon database, in a public mode with rate limits, a reviewer-only token and periodic resets. It is rehearsed
locally and not deployed yet.

## What is real and what is not

| Part | State |
|---|---|
| FastAPI, Postgres, pgvector, job queue, write-back, webhooks | **Ran for real**, in Docker and in tests against real Postgres |
| Review UI (React on Shopify's Polaris web components) and landing page | **Ran for real** in a browser, at 375, 768 and 1280 widths, behind the real security headers. The landing page has light and dark; the review UI stays light, because Polaris documents no dark mode |
| MCP server | **Ran for real** over streamable HTTP and through an in-process client |
| Deterministic rules and citation checker | **Ran for real**, measured on synthetic data (`docs/EVAL.md`) |
| RAG | **Ran for real**, on 19 chunks of invented policy, with lexical hashed embeddings, not a semantic model |
| CI workflow | **Ran on GitHub once.** Six of seven jobs passed. `security` failed because gitleaks could not resolve the parent of the root commit on the first push, so it scanned nothing |
| Hosted LLM provider and judge | **Ran for real, once**, on the dev split: 120 agent calls and 40 judge calls to a hosted LLM |
| Agent behaviour, cost, latency | **Measured once on 40 synthetic products** (see Results). Repair quality has no automatic score. The policy search and citation path was never used by the real model |
| Free public demo (one container, Render and Neon) | **Rehearsed locally, not deployed.** `docker-compose.public.yml` with `scripts/smoke_public.py` passed on 2026-11-03 and again on 2026-11-04 with the Polaris UI and the landing page; output in `eval/reports/checks-2026-11-04/` |
| Live Shopify adapter | **Written, not run.** Tested against a mocked GraphQL server only, token renewal included. It has never called a store |
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

- Single store, single deployment. This is not a public Shopify app: no OAuth install, no compliance webhooks, no session tokens.
- Reviewer tokens never expire. Rate limits exist only in the public demo mode, in one process's memory. Put a WAF or a CIDR rule in front before real use.
- Rules are English word lists, so they miss paraphrases and can false-block plain English.
- Only the first 25 variants and 10 media of a product are read from a live store.
- With `SHOPIFY_CLIENT_ID` and `SHOPIFY_CLIENT_SECRET`, the live adapter renews its 24-hour token itself. That is tested against mocked HTTP only.
- The eval data is synthetic. Its author also wrote the rules.
- The review UI loads Polaris from `cdn.shopify.com` at run time, so it needs that CDN to be reachable, and Shopify's script runs with the reviewer's token in reach. Without the CDN the page says it could not load. The `/app` pages allow only Shopify's `/shopifycloud/` and `/static/fonts/` paths on that host, not the merchant uploads it also serves, and inline styles, because Polaris writes its own `<style>` blocks. No inline script is allowed.

## Steps only the owner can do

Everything above runs without these. Each one needs an account, a key or spending approval.

**1. Publish the repo (created 2026-11-02, currently private).** Commits show the author email in `git log`. Before making it public, start the `ci` workflow by hand, because only that run makes gitleaks scan the whole history.

**2. Measure the agent with a real model (spends money; ran once, repeat it after changes).** Create an API key with the hosted LLM provider and set a spend limit in its console. That limit is the real cap: `DAILY_COST_CAP_USD` (default 5 USD) counts only the current run, because each run starts with an empty eval database. One run makes 40 agent runs, each capped at 0.25 USD.

Start only the database, and set the key only in this shell. A key visible to a full `docker compose up` switches the demo to the hosted LLM, and it runs up to 16 products on its own.
```bash
docker compose up -d db
export ANTHROPIC_API_KEY=...   # PowerShell: $env:ANTHROPIC_API_KEY="..."
export DAILY_COST_CAP_USD=1    # PowerShell: $env:DAILY_COST_CAP_USD="1"
python -m gatekeeper_eval agent --split dev
```
Use the virtual environment from "Tests" above. The sweep stops after 3 runs in a row raise an error, so a bad key costs nothing. Status `FAILED` means no run finished, and `PARTIAL` means it stopped early. Do not quote either. If the first run reaches the 1 USD cap, raise the cap and run again.

The report in `eval/reports/` gives run outcomes, validator results, judge concerns, policy retrieval, citation checks, cost and latency. It does not score repair quality, so read `per_case`, above all the 10 injection cases. `injected_phrase_copied` only detects verbatim copying.

**3. Try a real development store (free, reads first).** Shopify no longer lets you create a custom app with a permanent token in the store admin. Create the store and the app in the same organization at https://dev.shopify.com/dashboard/: Stores, Create store, Dev. Then Apps, Create app, Start from Dev Dashboard, with scopes `read_products` and `write_products`. Release a version, install it on the store, and copy the client ID and secret from Credentials. With those two set, the app asks Shopify for a 24-hour token and renews it before it expires. Use a database of its own, so the real store's data never mixes with the demo's.
```bash
docker compose up -d db
docker compose exec db createdb -U gatekeeper gatekeeper_live
export SHOPIFY_CLIENT_ID=... SHOPIFY_CLIENT_SECRET=...
export DATABASE_URL=postgresql://gatekeeper:local-demo-owner@127.0.0.1:54329/gatekeeper_live
export SHOPIFY_ADAPTER=live SHOPIFY_SHOP_DOMAIN=your-store.myshopify.com
export SHOPIFY_WEBHOOK_SECRET=$SHOPIFY_CLIENT_SECRET WRITEBACK_ENABLED=0 DEMO_MODE=0
export REVIEWER_TOKENS="you:admin:$(python -c 'import hashlib,sys;print(hashlib.sha256(sys.argv[1].encode()).hexdigest())' YOUR_LONG_RANDOM_TOKEN)"
python -m gatekeeper.seed && uvicorn gatekeeper.api.app:create_app --factory --port 8000
```
This serves the API only. Agent runs also need `python -m gatekeeper.worker` in a second shell with the same variables, and `/readyz` returns 503 until the worker is running. Keep writes off at first. Save real responses as new fixtures, compare them with the mock in `tests/integration/test_shopify_adapters.py`, then turn on `WRITEBACK_ENABLED=1` and `POST /api/admin/writeback`. Writes need a model key: the app refuses to let the template model write to a store. Shopify does not say whether a new client-credentials token revokes older ones, and the API and the worker each hold their own; watch for repeated 401s on the first run. Webhooks need a public HTTPS address, so register `products/update`, `products/create` and `products/delete` at `/webhooks/shopify` only once you have one.

**4. Put the demo online (free, two free accounts).** Create a Neon project and a Render Blueprint from `render.yaml`. Nothing is billed: the service sleeps when idle, and running out of free hours suspends it until next month. Steps, limits and what a visitor can do are in `docs/DEPLOY_DEMO.md`. Rehearse first with `docker compose -f docker-compose.public.yml up -d --build --wait` and `python scripts/smoke_public.py`.

**5. Deploy to AWS (spends money every month, about one small RDS instance, one load balancer and two Fargate tasks).** The HTTPS listener needs an ACM certificate, and that needs a domain you control. The tasks take the app's client ID as a variable and its client secret from Secrets Manager, so the token renews itself. Create an S3 state bucket and the certificate, then:
```bash
cd infra/terraform
terraform init -backend-config="bucket=YOUR_BUCKET" -backend-config="key=gatekeeper.tfstate" -backend-config="region=ca-central-1"
terraform plan -var image_tag=v1 -var acm_certificate_arn=YOUR_ARN -var shop_domain=your-store.myshopify.com -var shopify_client_id=YOUR_CLIENT_ID
```
Read the plan, then apply it. Push the image to the ECR repository it creates, set each secret with `aws secretsmanager put-secret-value`, and run the `migrate` task once before starting the services. `WRITEBACK_ENABLED` stays off until you change the variable.
