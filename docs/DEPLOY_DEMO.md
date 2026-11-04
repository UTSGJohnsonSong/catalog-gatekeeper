# Free public demo

State on 2026-11-04: **prepared and rehearsed locally, not deployed.** Deploying needs two free accounts that only
the owner can create. The rehearsal was repeated on 2026-11-04 with the Polaris review UI and the landing page.

The layout is one container on Render's free plan and one free Neon Postgres. Prices and limits below were read on
2026-11-03 from [Render](https://render.com/docs/free) and [Neon](https://neon.com/pricing). Check both pages again
before relying on them.

## What a visitor gets

- A landing page at `/`, and under `/app` the same review UI and pipeline as `docker compose up`: a simulated
  store, the template model, the 12 checks, approval of an exact diff, write-back with read-back, and "Edit as
  merchant" sending a signed webhook. Put the bare service URL on a CV; the page links to the demo itself.
- Sign-in with the reviewer demo token, which the page fills in. The admin demo token is refused, so visitors cannot
  switch write-back off, start syncs or queue agent runs.
- A shared sandbox. Every visitor sees the same queue. It returns to its first state on every boot and every
  `DEMO_RESET_MINUTES` (60 here). The reset clears the activity log too, and the page says so.

## What protects it

`PUBLIC_DEMO=1` refuses to boot unless all of this holds (`src/gatekeeper/config.py`, `src/gatekeeper/allinone.py`):

- demo mode on, the fixture store, no model key, no store credentials;
- `TRUSTED_PROXY_HOPS` set on purpose, a webhook secret for the simulated edit, and a reset interval of at least 5
  minutes;
- `APP_DB_PASSWORD` set, so the API and worker never run as the owner role;
- a database that holds nothing but this demo. That check runs before setup touches anything, because setup migrates
  and resets role passwords. It refuses another shop's row, any run of a real model, and any product missing from
  the demo's own fixture store.

While running (`src/gatekeeper/api/app.py`, `src/gatekeeper/api/limits.py`):

- rate limits per client and overall, separately for changes (20 and 300 a minute) and reads (300 and 1200);
- request bodies over 64 KiB refused before parsing, the webhook route included;
- no `/docs` or `/openapi.json`, and HSTS on every response;
- the API and worker connect as the least-privilege app role. Only the setup process holds the owner connection, so
  the API cannot truncate tables or rewrite the audit log through SQL. This is not a sandbox: all three processes run
  as the same user, so code execution inside the API could still read the owner URL from the setup process.

The reset (`src/gatekeeper/seed.py`) truncates only the tables visitors can change, after the same database check. It
takes every table lock at once without waiting, so it cannot deadlock with a visitor's approval; if a table is busy
it retries for about five seconds, then gives up without touching anything. The worker is stopped while it runs.
A reset that fails or is skipped is retried within a minute; five failures in a row end the container, so the
platform restarts it and the boot reset reports the cause. Each finished reset is recorded as the `demo_reset`
heartbeat.

## Limits to know

- **Cold start.** A free Render service sleeps after 15 minutes without traffic, and the next visit waits about a
  minute. Say so next to the link, or the first visitor may think it is down.
- **Monthly quotas.** Render gives 750 free instance hours per workspace. Neon's free compute is 100 CU-hours a month
  and suspends after 5 idle minutes. The worker polls the database every second, so the database stays awake exactly
  while the service is awake. Do not add an uptime pinger: it would keep both awake and use up Neon's hours mid-month.
  Running out suspends the service until the next month. Nothing is billed.
- **Rate limits live in memory** and reset on every restart. One process enforces them, which is the layout here.
- **Proxy hop count.** Render does not document how many proxies append to `X-Forwarded-For`. `1` is the safe guess:
  if it is really 2, visitors share buckets, which errs towards limiting. See "Check the client address" below.
- **Visitor text** (reject notes, simulated edits) is visible to other visitors until the next reset. The UI escapes it.
- **Shopify's CDN.** The review UI loads Polaris from `cdn.shopify.com` in the visitor's browser, so `/app` needs that
  CDN. Its Content-Security-Policy allows only the `/shopifycloud/` and `/static/fonts/` paths there, plus the inline
  `<style>` blocks Polaris writes. The landing page and the API allow no other origin. If the CDN is blocked, `/app` says the review screen could not load.

## Rehearse locally

```bash
docker compose -f docker-compose.public.yml up -d --build --wait
python scripts/smoke_public.py
```

This runs the same container command against its own database on port 8001. The smoke test checks both pages and
their security headers, the token rules, the review flow with the worker in the same container, the body cap and the
rate limit.

## Deploy

1. **Neon.** Create a free project in the region closest to Render's Oregon region (AWS US West 2). Copy the
   **direct** connection string, not the one whose host contains `-pooler`: migrations and the write-back lock use
   session advisory locks, which the pooled connection does not support. Keep `sslmode=require`.
2. **Render.** New, then Blueprint, then connect this repository. Render needs read access to the repository; it can
   stay private. Render reads `render.yaml`, asks for `DATABASE_URL`, and generates the app-role password and the
   webhook secret. Apply.
3. **Check it.** Open the service URL and sign in with the filled-in token. Then run the smoke test against it:

   ```bash
   SMOKE_BASE=https://YOUR-SERVICE.onrender.com python scripts/smoke_public.py
   ```

Never set a model key or store credentials on this service. It refuses to boot with them, because any visitor could
spend them.

## Check the client address

The smoke test above already checks the outcome that matters: after using up its allowance it claims a new address
in `X-Forwarded-For` and expects to be refused anyway. If that check fails, the proxy passes the client's header
through and the hop count trusts a forged address.

To see the count itself, restart the service, then send one request with a made-up address:

```bash
curl -s -o /dev/null -H "X-Forwarded-For: 203.0.113.9" https://YOUR-SERVICE.onrender.com/api/meta
```

For its first five requests after a boot the API logs a line like `client address check: X-Forwarded-For has 2
entries, TRUSTED_PROXY_HOPS=1`. It logs the count only, never the addresses.

| Entries for that request | Meaning | Set `TRUSTED_PROXY_HOPS` to |
|---|---|---|
| 2 | One proxy appended your real address | 1 |
| 3 | Two proxies appended | 2 |
| 1 | The proxy passed the header through or replaced it | 0. Visitors then share one bucket per proxy address, and the overall limits still hold |

## Turn it off

Suspend the service in Render. The Neon project keeps the data until you remove it.

## Hosts considered and not chosen

| Host | Why not |
|---|---|
| Northflank free sandbox | Always on, two services and a database. Its billing docs say every account must add a payment method, and charges past the free allowance are not ruled out, so it is not reliably free |
| Koyeb free | One free service that sleeps after an hour. Its free Postgres allows 5 active hours a month |
| Fly.io | No free allowance for new customers |
| Railway | 5 USD a month |
| AWS (`infra/terraform`) | The production layout, tens of dollars a month. Not for a demo |
