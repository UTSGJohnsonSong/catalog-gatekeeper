# Catalog Gatekeeper

An AI agent that audits and repairs a Shopify-style product catalog. It proposes fixes, cites the store policy it relied on, passes every proposal through checks a program can verify, queues it for a person, and only then writes back.

**Why it exists.** An LLM can write a good product description, and it can also invent a warranty or follow an instruction hidden in a supplier's text. The interesting problem is what stands between the model and the store. Here that is a narrow tool surface, 12 deterministic checks, citations checked word for word, a person approving an exact diff, and a write-back that refuses to overwrite a merchant's newer edit.

**The demo runs with no account and no key, so two parts are simulated.** The store is a fixture, and the model is a template script, not an LLM. The eval data is synthetic. See the table below.

## Run it

```bash
docker compose up --build
```

Open http://127.0.0.1:8000 and sign in with the filled-in demo token. Open a fix, read the diff and the checks, approve it, and watch it get written. "Edit as merchant" on the Products page sends a signed webhook, and any open fix for that product goes out of date.

```bash
python scripts/smoke.py
```

That drives the same flow over HTTP against the running stack.

## What is real and what is not

| Part | State |
|---|---|
| FastAPI, Postgres, pgvector, job queue, write-back, webhooks | **Ran for real**, in Docker and in tests against real Postgres |
| React review UI | **Ran for real** in a browser, at 375, 768 and 1280 widths, light and dark |
| MCP server | **Ran for real** over streamable HTTP and through an in-process client |
| Deterministic rules and citation checker | **Ran for real**, measured on synthetic data (`docs/EVAL.md`) |
| RAG | **Ran for real**, on 19 chunks of invented policy, with lexical hashed embeddings, not a semantic model |
| CI workflow | **Written, not run.** Each job's commands ran locally, but it has never run on GitHub, because no repo exists yet |
| Hosted LLM provider and judge | **Written, not run.** Tested against a fake client. No key was available, so no model has been called |
| Agent accuracy, cost, latency | **Not measured.** Needs a key: `python -m gatekeeper_eval agent` |
| Live Shopify adapter | **Written, not run.** Tested against a mocked GraphQL server only. It has never called a store |
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
- Reviewer tokens have no rate limit or expiry. Put a WAF or a CIDR rule in front before real use.
- Rules are English word lists, so they miss paraphrases and can false-block plain English.
- Only the first 25 variants and 10 media of a product are read from a live store.
- The eval data is synthetic. Its author also wrote the rules.

## Steps only the owner can do

Everything above runs without these. Each one needs an account, a key or spending approval.

**1. Publish the repo (needs your yes, because a public repo cannot be taken back).**
```bash
gh repo create catalog-gatekeeper --public --source . --push
```
Then enable secret push protection in the repo's settings. CI runs on the first push; fix anything it finds before citing it.

**2. Measure the agent with a real model (spends money, capped by `DAILY_COST_CAP_USD`, default 5 USD).** Create an API key with the hosted LLM provider, with a spend limit, then:
```bash
export ANTHROPIC_API_KEY=...   # PowerShell: $env:ANTHROPIC_API_KEY="..."
docker compose down -v && docker compose up --build
python -m gatekeeper_eval agent --split dev
```
Read the report in `eval/reports/`. This is the first run of the real provider, so expect to fix something.

**3. Try a real development store (free, reads first).** Sign up at partners.shopify.com, create a development store, create a custom app with `read_products` and `write_products`, and copy its Admin API token and client secret.
```bash
export SHOPIFY_ADAPTER=live SHOPIFY_SHOP_DOMAIN=your-store.myshopify.com
export SHOPIFY_ACCESS_TOKEN=... SHOPIFY_WEBHOOK_SECRET=... WRITEBACK_ENABLED=0 DEMO_MODE=0
export REVIEWER_TOKENS="you:admin:$(python -c 'import hashlib,sys;print(hashlib.sha256(sys.argv[1].encode()).hexdigest())' YOUR_LONG_RANDOM_TOKEN)"
python -m gatekeeper.seed && uvicorn gatekeeper.api.app:create_app --factory --port 8000
```
Keep writes off at first. Save real responses as new fixtures, compare them with the mock in `tests/integration/test_shopify_adapters.py`, then turn on `WRITEBACK_ENABLED=1` and `POST /api/admin/writeback`. Register webhooks for `products/update`, `products/create` and `products/delete` pointing at `/webhooks/shopify`.

**4. Deploy to AWS (spends money every month, about one small RDS instance, one load balancer and two Fargate tasks).** Create an S3 state bucket and an ACM certificate, then:
```bash
cd infra/terraform
terraform init -backend-config="bucket=YOUR_BUCKET" -backend-config="key=gatekeeper.tfstate" -backend-config="region=ca-central-1"
terraform plan -var image_tag=v1 -var acm_certificate_arn=YOUR_ARN -var shop_domain=your-store.myshopify.com
```
Read the plan, then apply it. Push the image to the ECR repository it creates, set each secret with `aws secretsmanager put-secret-value`, and run the `migrate` task once before starting the services. `WRITEBACK_ENABLED` stays off until you change the variable.
