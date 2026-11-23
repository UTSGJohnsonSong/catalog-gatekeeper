# On-call runbook

**Status of this document:** written from the design and exercised only against the local demo. The service has never run in production, so the alert thresholds are untested starting points.

## First five minutes

1. Check `GET /readyz`, which reports database, migrations and worker heartbeat age, and `GET /api/ops`, which counts dead jobs, failed and stuck writes, and failed runs. The queue page shows the same counts.
2. Check queue age and dead jobs:
   ```sql
   SELECT status, count(*), min(run_after) FROM jobs GROUP BY status;
   SELECT id, kind, attempts, last_error FROM jobs WHERE status = 'dead' ORDER BY id DESC LIMIT 20;
   ```
3. If writes look wrong, flip the kill switch first and investigate second:
   ```bash
   curl -X POST "$HOST/api/admin/writeback" -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' -d '{"enabled": false}'
   ```

## Service levels to watch

| Signal | Target | Where |
|---|---|---|
| Webhook acknowledged | under 5 s (Shopify's limit) | ALB target response time |
| Queue age | under 2 minutes | `jobs` table |
| Write-back success | no `failed` without a known cause | `SELECT status, count(*) FROM proposals GROUP BY status` |
| Worker heartbeat | under 120 s | `/readyz` |

Logs are JSON on stdout, with `job_id`, `kind`, `proposal_id`, `run_id` where they apply. Call sites pass ids and error text, not tokens, HMAC values or product text. That is a convention, not a filter: exception text is logged as raised and nothing scrubs it. uvicorn's own access log is plain text.

## Symptoms

| Symptom | Likely cause | Action |
|---|---|---|
| Spike of 401 on `/webhooks/shopify` | Webhook secret mismatch or a recent rotation. Shopify says a new secret can take up to an hour to apply | Compare `SHOPIFY_WEBHOOK_SECRET` with the app's client secret. Do not disable verification |
| `/readyz` 503, `database: false` | RDS down, or credentials rotated | Check RDS events, then the `database-url-app` secret |
| `/readyz` 503, old heartbeat | Worker crashed or stuck | Check worker logs and restart the ECS service. Jobs are safe: the reaper requeues after 10 minutes |
| Dead jobs growing | A persistent error, shown in `last_error` | Fix the cause, then requeue: `UPDATE jobs SET status='queued', attempts=0, run_after=now() WHERE status='dead' AND id=...` |
| Proposals stuck in `applying` | Worker died mid-write | The sweeper requeues after 2 minutes, and the advisory lock prevents a double writer. After 5 real write attempts the proposal becomes `failed`. A dead job is not revived automatically: fix the cause, then requeue it |
| `failed` with `verification_mismatch` or `outcome_unknown` | The store changed the text on write, a merchant edit raced us, or a response was lost after a write. The text may be live | Compare the store with `writebacks.before_snapshot` and `request`. If it is a normalization pattern, extend `html_canon` and add a test. Expect this on the first live runs |
| Spike of `stale` | Merchants editing during review, or a webhook flood | Usually correct behaviour. Re-run the agent on those products |
| `budget_exceeded` or daily cap reached | Model spending more than expected | Read `agent_runs` and `llm_calls`. Raise the cap only with a reason. Do not fall back to the template model in production: it refuses to boot there unless `ALLOW_DEMO_MODEL_IN_PROD` is set, which you should not do |
| `no_finish` or `failed` runs rising | Model outage, or behaviour change after a model update | Check `agent_runs.error`. Pin `LLM_MODEL` and rerun the live eval |
| Shopify throttling | Bucket exhausted during a large sync | Jobs defer themselves. Reduce sweep size if persistent |
| Webhooks stop arriving | Shopify deletes a subscription after 8 failed deliveries over 4 hours | Re-register the subscription, then run a sync. The hourly sync covers 2 hours back, and the daily full sync covers anything older |
| False-block complaint | A lexical rule hit plain English | Read the evidence on the proposal. Add a hard negative to `eval/datasets` first, then change the rule, then run the gate |
| `401` from the token endpoint, jobs dead with "token request rejected" | The client secret was rotated or the app was uninstalled | Update `shopify-client-secret` and redeploy. The adapter asks for a new token on the next call |
| Public demo: visitors get `429` | One address used its allowance, or the whole demo is busy, which is logged as "the demo-wide rate limit was reached" | Expected under load. If honest visitors share one bucket, the proxy hop count is wrong in either direction: a count that is too low or too high both make visitors share. Read the "client address check" lines as `docs/DEPLOY_DEMO.md` explains |
| Public demo: "demo reset failed, N in a row" | A table stayed busy, the database connection dropped, or the demo could not be loaded again after it was emptied. The demo may be empty until a reset succeeds | It retries within a minute. After five failures in a row the container exits and the platform restarts it; read the boot log for the cause |
| `/app` says "The review screen could not load" | The browser could not fetch Polaris from `cdn.shopify.com`: a network block, a content blocker, or a CDN outage. The API is unaffected | Open `https://cdn.shopify.com/shopifycloud/polaris-2.js` from the same browser. If it loads, check the browser console for a Content-Security-Policy refusal, which would mean the `/app` policy in `src/gatekeeper/api/app.py` no longer matches what Polaris loads |
| Public demo: "demo reset cannot succeed" or a boot that refuses the database | The database holds another shop, a real model's runs, or products from outside the demo store | `DATABASE_URL` points at the wrong database. Stop the service and fix it. Nothing was changed: the check runs before setup |
| Public demo: "demo reset skipped" as an error | Another session has held the reset lock for several minutes | Find it in `pg_stat_activity` by `application_name = 'gatekeeper-reset'` and end it |
| Public demo boot loops with "PUBLIC_DEMO must not hold" | A model key or store credentials were added to the demo's environment | Remove them. A public demo never holds either |

## Procedures

**Restore a product after a bad write.** `writebacks.before_snapshot` holds the exact prior values. Restoring is a manual `productUpdate` with those values. There is no automatic revert in v1, on purpose: a revert is itself a write, and it should go through a person.

**Rotate reviewer tokens.** Update the `reviewer-tokens` secret and redeploy. Sessions hold only the token, so old tokens stop working at once.

**Rotate the webhook secret.** Rotate in Shopify, update the secret, redeploy. Expect a window of up to an hour of 401s, covered by Shopify's retries and the scheduled syncs.

**Restore the database.** RDS keeps 7 days of automated backups. Restore to a new instance, update `database-url-*`, run the migrate task, then run a full sync. Proposals in `applying` at the restore point are recovered by the sweeper, which compares against the store before writing.

**Bump the Admin API version (quarterly).** Change `ADMIN_API_VERSION` in `src/gatekeeper/config.py`, run the contract tests, then run a read-only sync against the dev store and save real responses as new fixtures before enabling writes.

**Roll back a deploy.** ECS circuit breaker rolls back a failing deploy automatically. To roll back by hand, redeploy the previous image tag. Migrations here are additive, so old code keeps working against the new schema. The down files, run newest first, tear the whole schema down and drop its data; a test runs them and checks that no table is left.

### Embedded app (`docs/EMBEDDED.md`)

**"the app is not connected to this store" in the logs.** No usable store token is stored: nobody has opened the app since install, the merchant uninstalled it, Shopify refused the refresh token, or `TOKEN_ENCRYPTION_KEY` changed. Scheduled syncs are skipped, not failed. Opening the app once from the store's admin exchanges a new token and queues a sync.

**Rotate `TOKEN_ENCRYPTION_KEY`.** Set the new key and redeploy. The stored token no longer decrypts, which the log says once, and the next open reconnects the store. Nothing else is encrypted with it.

**Rotate the client secret.** Rotate it in the Dev Dashboard, update `SHOPIFY_CLIENT_SECRET`, redeploy. It signs the webhooks and the session tokens and is sent in token requests, so all three fail until the service has the new one.

**After an uninstall.** `app/uninstalled` deletes the token, pauses writing and drops queued syncs and runs. `shop/redact` follows 48 hours later and deletes the store's catalog and the work derived from it; the audit log keeps its rows. The two customer privacy webhooks are answered with a record that no customer data is held.
