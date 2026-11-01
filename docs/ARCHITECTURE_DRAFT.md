> **Deprecated 2026-10-31.** This Rails design was replaced by `docs/ARCHITECTURE.md` (Python, FastAPI, React). See `docs/adr/0001-python-over-rails.md` for why. Kept as a record; do not build from it.

# Catalog Gatekeeper — Architecture Blueprint (DRAFT)

> Status: draft for owner review, 2026-10-27. No code has been written yet.
> Produced by the `feature-dev:code-architect` agent; facts were checked against the sources listed at the end on 2026-10-27.
> Open owner decisions are listed in "Pending decisions" at the bottom.

This builds on existing evidence. Shell's SQL allowlist and permission checks become validator rules V1–V4. CardioTrace's "regression test per real bug" and brute-force reference implementations become the test style. JobOS's hashed confirmation becomes `approved_hash`. The evidence handbook marks JobOS replay protection as unproven, so here replay is enforced and tested. The eval harness is the answer to the Shell "92% / 300+" claim, which had no denominator and no pass rule.

## 1. Stack and reasons

- **Ruby 3.4.x (pinned), Rails 8.1, PostgreSQL 17, Solid Queue.**
  - The Rails guide targets 8.1 with Ruby 3.2 or newer.
  - Ruby 4.0.x is the newest stable, but gem compatibility is unverified, so 4.0 is a stretch upgrade.
  - Solid Queue needs no Redis and enqueues inside the transaction.
- **Windows: WSL2 Ubuntu plus mise.** Use VS Code Remote-WSL. Docker runs only Postgres and the production image.
  - The Rails install guide recommends WSL on Windows, and WSL matches the Linux CI.
  - Docker-only development was rejected. Bind mounts are slow, and the IDE setup is awkward for the pairing round.
  - RubyInstaller+DevKit was rejected. It works, but it drifts from the Linux CI.
  - WSL2 and Ubuntu are already installed on this machine, checked with `wsl -l -v` on 2026-10-27.
- **`shopify_api` (client, token exchange, refresh) without the `shopify_app` engine.**
  - `shopify_app` is maintained and supports Rails 7.1 up to, but not including, 9.
  - The Ruby template repo is not archived, but it uses Polaris React and Vite. The official scaffold doc promotes only React Router.
  - The session-token concern and the HMAC verifier are hand-written, about 150 lines, so every line can be explained.
- **The Admin API is pinned to `2026-10`,** the latest stable version. It lives in one constant that is bumped quarterly.
- **UI: ERB, Turbo and Polaris web components.**
  - Pages load `polaris-2.js`, `app-bridge.js` and the `shopify-api-key` meta tag.
  - The Polaris React repo is marked deprecated, while the web components work in server-rendered pages.
  - There is no JS build step.
- **LLM: one `Llm::Adapter` over Faraday with JSON-schema output.**
  - Configuration comes from env vars: `LLM_PROVIDER` (`anthropic`, `openai` or `fake`), `LLM_API_KEY`, `LLM_MODEL`, and optionally `LLM_JUDGE_MODEL`.
  - Tests use `fake` plus WebMock with net connect disabled.
- **Tooling:** RSpec, WebMock, FactoryBot, SimpleCov, rubocop-rails-omakase, Brakeman, bundler-audit. The repo is Ruby only.

## 2. Layout

```
shopify.app.toml shopify.web.toml compose.yaml Dockerfile mise.toml .github/workflows/ci.yml
app/clients/shopify/  admin.rb(port) fixture_admin.rb live_admin.rb throttle.rb token_provider.rb queries/*.graphql
app/clients/llm/      adapter.rb anthropic.rb openai.rb fake.rb
app/controllers/      products proposals reviews syncs health; webhooks/shopify_controller.rb; concerns/session_token_auth.rb
app/jobs/             sync_products propose_fix validate_proposal apply_writeback process_webhook reconcile
app/models/           shop product proposal validation_result review writeback webhook_event sync_run llm_call
app/services/         sync/ proposals/(proposer, prompts/*.erb) validation/(pipeline, judge, rules/*.rb) writeback/applier.rb webhooks/(verifier, router) content_hash.rb
app/views/            layouts/embedded.html.erb products/ proposals/
config/gatekeeper/policy.yml   db/migrate/   lib/tasks/{shopify,eval}.rake
eval/ cases/v1/{dev,test}.jsonl generator/ runner.rb metrics.rb reports/ DATASHEET.md NOTICE
spec/{models,requests,jobs,clients,services,eval,support}  spec/fixtures/shopify/  docs/{runbook.md,adr/}
```

## 3. Data model

Every table carries `shop_id`, and every query is scoped through `Current.shop`. There is one reversible migration per table, with foreign keys, NOT NULL, and check constraints on status columns.

- **shops:** `shopify_domain` (unique); `access_token` and `refresh_token` stored with `encrypts`; `token_expires_at`; `refresh_expires_at`; `scopes`; `uninstalled_at`; `writeback_enabled` (default false).
- **products:** unique on (`shop_id`, `gid`). Fields: title, handle, `description_html`, `seo_title`, `seo_description`, `tags` (text[]), `product_type`, vendor, status, `price_min`/`price_max`, `attrs` (jsonb: options, metafields, category), `shopify_updated_at`, `content_hash`, `deleted_at`.
- **proposals:** `base_content_hash`, `proposed` (jsonb), status, `risk_score`, `prompt_version`, `llm_model`. Unique on (`product_id`, `base_content_hash`), plus a partial unique index so a product has at most one open proposal.
- **validation_results:** `rule_id`, `rule_version`, kind, severity, passed, `evidence` (jsonb). Unique on (`proposal_id`, `rule_id`).
- **reviews:** `reviewer_sub`, decision, note, and `approved_hash`, which is sha256 of the base hash plus the canonical proposed content.
- **writebacks:** unique `proposal_id` and unique `idempotency_key`; status, attempts, `before_snapshot`, request, response, `user_errors`, cost.
- **webhook_events:** unique `webhook_id`; `event_id`, topic, `triggered_at`, payload, status, error.
- **sync_runs** (cursor, cost) and **llm_calls** (role, model, tokens, latency).

## 4. Flows

1. **Sync.**
   - Call `Admin#products(first: 50, query: "updated_at:>cursor")` with minimal fields.
   - Upsert each page, compute `content_hash`, and advance the cursor after commit.
   - A cheap deterministic lint picks flawed listings, so the LLM runs only when needed.
2. **Propose.**
   - The prompt is versioned, and product text is fenced off as untrusted data.
   - The LLM returns JSON: `{descriptionHtml, seoTitle, seoDescription, rationale}`.
   - The model has no tools and cannot change any other field.
3. **Validate.**
   - If any hard rule fails, the proposal is `blocked`. It is still shown, but it cannot be approved.
   - Otherwise it moves to `pending_review` with a risk score.
4. **Review.**
   - An embedded Polaris queue shows a diff, the rule evidence and the risk score.
   - Approving requires a verified session token and stores `approved_hash`.
   - Rejecting requires a reason.
5. **Write-back.**
   - Compare-and-set the status from `approved` to `applying`.
   - Recompute `approved_hash` and check the kill switch.
   - Re-fetch the product and compare hashes:
     - current equals base: run the mutation;
     - current equals proposed: mark `applied` (crash recovery);
     - anything else: mark `stale`.
   - Save `before_snapshot`, then call `productUpdate(product: {id, descriptionHtml, seo})` with absolute values.
   - `userErrors` are terminal. Throttling and 5xx errors use `retry_on` with backoff.
6. **Webhooks** (`POST /webhooks/shopify`).
   - On receipt:
     - read the raw body and verify the HMAC;
     - run `INSERT ... ON CONFLICT (webhook_id) DO NOTHING`;
     - enqueue a job and return 200 quickly.
   - Processing:
     - The job treats the payload only as a signal and re-fetches the product over GraphQL, because delivery order is not guaranteed.
     - An echo check drops events whose hash equals a write-back's proposed hash.
     - An hourly `reconcile` job catches missed events.
   - Compliance webhooks:
     - `customers/data_request` and `customers/redact` return 200, because no customer data is stored.
     - `shop/redact` deletes the shop's rows.
     - `app/uninstalled` revokes the tokens.

## 5. Validator design

**Deterministic rules (hard block):**
- V1, field allowlist: only `descriptionHtml` and `seo.*` may change.
- V2, HTML allowlist: no new URLs, emails or phone numbers.
- V3, number and unit fidelity: every number with a unit must appear in the source, and any price must equal the variant price.
- V4, attribute contradiction: checked against Shopify product-taxonomy vocabulary.
- V5, fact preservation and an edit-ratio cap.
- V6, policy lexicon from `policy.yml`: medical claims, guarantees, competitor names, price promises.
- V7, SEO length and language.
- V8, injection residue.

**LLM-judged rules (advisory only):**
- J1, unsupported qualitative claim. The judge must return a quote, and code checks that the quote is a verbatim substring of the output. Judge output that cannot be verified is discarded.
- J2, meaning drift.
- J3, tone.

**How they combine:**
- The LLM can raise the risk score, but it can never block a proposal or clear a deterministic failure.
- If the judge is down, the result is `unavailable` with risk +25. It never passes silently.
- When configured, the judge uses a different model from the proposer.

## 6. Eval harness

- **Case format:** JSONL with `{id, split, category, product, proposal, injected[{rule_id, operator, span}], expect{block, rules[]}, origin, schema_version}`.
- **How the cases are built honestly:**
  - A seeded generator makes 120 base products from the Shopify product-taxonomy vocabulary (MIT licence; use the release assets).
  - About 30 descriptions are hand-written. LLM-drafted text is tagged `origin: llm_drafted`.
  - Deterministic mutation operators inject the defects, so every label is true by construction.
  - The mix is 120 faulty cases (12 defect classes × 10), 50 easy clean cases and 30 hard negatives.
  - The split is 100 dev and 100 test. The test split is frozen, hashed, and run once per release.
  - No scraped or marketplace data is used. `DATASHEET.md` and `NOTICE` carry the licence note and the synthetic caveat.
- **Metrics:**
  - Precision, recall and F1, per rule and overall, with Wilson 95% CIs. The report states that n is small.
  - False-block rate on clean cases.
  - J1 agreement on a 40-case subset labelled by one annotator.
  - End-to-end residual defect rate before and after, measured by an independent oracle.
  - Cost and latency per proposal.
- **Modes:**
  - `rake eval:validators` runs in CI and gates on recall and false-block thresholds.
  - `eval:e2e:replay` uses committed recordings.
  - `eval:e2e:live` is run only by a human.
  - Every report header records the git SHA, dataset hash, model, prompt version and date.

## 7. Testing and CI

- **Unit specs:** each rule, the content hash, and the state machine.
- **Request specs:**
  - webhooks: valid, invalid, replayed, out of order;
  - session tokens: expired, wrong `aud`;
  - cross-tenant access.
- **Job specs:** throttle retries.
- **Contract tests:** `FixtureAdmin` and `LiveAdmin` must pass one shared contract spec.
- **Concurrency spec:** two threads apply the same proposal, and exactly one mutation is sent.
- **No live network:** WebMock `disable_net_connect!` is set globally.
- **CI (GitHub Actions):**
  - a Postgres service;
  - checks: rubocop, brakeman, bundler-audit, gitleaks, rspec with a SimpleCov floor, and the `eval:validators` gate;
  - a Docker build.
  - Actions are pinned by SHA, and only fake test keys are used.

## 8. Runbook outline

- **Dependencies and SLOs:**
  - webhooks are acknowledged in under 5 s;
  - queue age;
  - write-back success rate.
- **Logs:** structured, with shop, `proposal_id`, `webhook_id` and query cost. Tokens and HMAC parameters are filtered out.
- **Symptom → cause → action table:**
  - a spike of 401s on webhooks;
  - a throttle backlog;
  - token refresh failure;
  - an LLM outage;
  - a spike of `stale` proposals;
  - a subscription deleted after repeated failed deliveries.
- **Recovery:**
  - replay `webhook_events`;
  - revert a write-back from `before_snapshot`, filed as a new proposal;
  - the `writeback_enabled=false` kill switch;
  - key rotation.
- **Quarterly checklist** for upgrading the API version.

## 9. Day plan (2–3 h/day, plus 30 min of Ruby practice)

| Day | Work |
|---|---|
| 1 | Scaffold, Postgres, CI skeleton, migrations, `FixtureAdmin`, sync |
| 2 | LLM adapter and fake, proposer, dev auth stub, Polaris layout |
| 3 | V1–V8 by TDD, pipeline, risk score |
| 4 | Judge port, review UI with diff, state machine, generator v0 |
| **5** | **Demo slice (below), tag v0.1, apply and reach out** |
| 6 | `LiveAdmin`, throttle, client-credentials provider, recorded fixtures, contract spec |
| 7 | Session-token auth, token exchange with expiring tokens, hardened write-back, live round trip |
| 8 | Webhooks, reconcile, compliance, TOML deploy |
| 9 | 200 cases, runner, metrics, freeze the test split |
| 10 | Tune on dev only, one test run, replay recordings, report |
| 11 | CI gates, security pass, runbook, `/review-pr` |
| 12 | README, ADRs, video, site and resume ("In progress" where true) |

**Day-5 slice:**
- Setup: `bin/dev` and `bin/rails db:seed` load 40 synthetic products through `FixtureAdmin`, behind the dev auth stub.
- The Products page shows lint badges.
- "Propose fixes" runs the real job path. It uses the fake LLM, or a real one if a key is set.
- The queue shows a before/after diff, V1–V8 evidence and the risk score. Blocked items have no Approve button.
- Approve, then Apply, updates the `FixtureAdmin` store, and the product shows `applied`. Reject records a reason.
- Also required: at least 80 green specs, green CI, a README with a diagram, and a 90-second video.
- Not in the slice: the live store, embedded auth, webhooks, throttling, eval numbers, and a real judge.
- Outreach describes it honestly: "built against recorded fixtures; live-store integration in progress".

## 10. Human-only tasks

| Task | Needed by |
|---|---|
| Approve installing mise, Ruby 3.4 and Rails inside WSL Ubuntu | before day 1 |
| Create the GitHub repo and enable push protection | day 1 |
| Sign up for a Shopify Partner account | start day 1; hard deadline day 5 |
| Create a dev store | day 5 evening |
| Create the Dev Dashboard app and put the client id and secret into `.env` (never committed) | day 6 |
| Install the Shopify CLI (`npm i -g @shopify/cli`) | day 6 |
| Run `bin/rails db:encryption:init` and keep the keys in a password manager | day 1 (dev), day 7 (live) |
| Provide an LLM key with a spend cap | optional on day 2; needed on days 9–10 |
| Send the referral or outreach message, apply, record the video | day 5 / day 12 |

## 11. Interview-ready decisions

1. **`shopify_api` without the `shopify_app` engine.**
   - Fewer hidden callbacks and testable auth.
   - It is also the Rails practice the JD asks for.
2. **An Admin API port with Fixture and Live adapters, plus a client-credentials token provider.**
   - Days 1–5 need no store.
   - The live store plugs in on day 6.
3. **Deterministic rules block; the LLM judge only escalates, with verifiable quotes.**
4. **The webhook is only a signal.**
   - The app re-fetches on each notification.
   - It dedupes on `X-Shopify-Webhook-Id`.
   - An hourly reconcile catches missed events.
5. **Idempotent write-back.**
   - Compare-and-set, with content-hash preconditions and a unique key.
   - Crashes are reconciled. `@idempotent` support for `productUpdate` is unconfirmed.
6. **Cost-aware throttling.**
   - It reads `throttleStatus` and sleeps for (cost − available) / restoreRate.
   - It backs off with jitter.
7. **Polaris web components in ERB,** pinned to `polaris-2.js`.
8. **A synthetic eval set, labelled by construction, with a frozen test split.**
   - The licences are clear, and the labels are not circular.
   - Its limit: synthetic defect rates are not real-world prevalence.

**Security notes:**
- The dev auth stub refuses to boot outside development and test.
- Token refresh and exchange are serialized per shop with a row lock.
- Embedded pages need a frame-ancestors CSP.
- CI, auth, HMAC, token storage and write-back all trigger `/review-pr` and a security check.

## Unverified

- Whether `productUpdate` supports `@idempotent`.
- Whether the App Bridge fetch interceptor works with Turbo navigation.
- Whether the gems work on Ruby 4.0.
- Whether dev stores are free.
- Whether Node 24 is compatible with the Shopify CLI.

## Pending decisions (owner)

1. **Repo location.** The owner chose a folder on the Windows D: drive. The blueprint prefers the WSL Linux filesystem for speed. Options:
   - (a) keep the repo on `D:` and run Rails from `/mnt/d` (slower I/O);
   - (b) develop in WSL `~/code` and treat `D:` as a mirror.
2. **Approval to install** mise, Ruby 3.4 and Rails inside WSL Ubuntu.

## Sources (fetched 2026-10-27)

https://shopify.dev/docs/api/usage/versioning ·
https://shopify.dev/docs/apps/build/scaffold-app ·
https://github.com/Shopify/shopify-app-template-ruby ·
https://github.com/Shopify/shopify_app ·
https://rubygems.org/gems/shopify_app ·
https://rubygems.org/gems/shopify_api ·
https://raw.githubusercontent.com/Shopify/shopify-api-ruby/main/docs/usage/oauth.md ·
https://shopify.dev/docs/apps/build/webhooks/subscribe/https ·
https://shopify.dev/docs/apps/build/privacy-law-compliance ·
https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens/token-exchange ·
https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens/client-credentials-grant ·
https://shopify.dev/docs/apps/build/authentication-authorization/session-tokens ·
https://shopify.dev/docs/api/admin-graphql/latest/mutations/productUpdate ·
https://shopify.dev/docs/api/admin-graphql/latest/queries/products ·
https://shopify.dev/docs/api/usage/idempotent-requests ·
https://shopify.dev/docs/apps/build/apis/graphql-admin/rate-limits ·
https://shopify.dev/docs/api/app-home/latest/web-components/versioning.md ·
https://raw.githubusercontent.com/Shopify/polaris/HEAD/README.md ·
https://shopify.dev/docs/apps/build/dev-dashboard/development-stores ·
https://shopify.dev/docs/apps/build/cli-for-apps/app-configuration ·
https://github.com/Shopify/product-taxonomy ·
https://guides.rubyonrails.org/getting_started.html ·
https://guides.rubyonrails.org/install_ruby_on_rails.html ·
https://guides.rubyonrails.org/active_record_encryption.html ·
https://www.ruby-lang.org/en/downloads/ ·
https://rubyinstaller.org/downloads/
