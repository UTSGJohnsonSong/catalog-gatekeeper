Checks run on 2026-11-04 for the review UI on Shopify's Polaris web components and the new landing page.

How the containers ran: as on 2026-11-03, the existing catalog-gatekeeper:local image ran with this tree's src/,
fixtures/ and a fresh frontend build mounted read-only over the copies baked into it. Python dependencies did not
change. The regular smoke test ran against a fresh copy of `docker compose up` under its own project name, so it
started from the demo's first state. The CI demo-smoke job builds the image from scratch.

Files:
- pytest.txt: the full backend suite against real Postgres, with coverage. 600 passed.
- frontend.txt: type check, unit tests (51 passed), and the production build of both pages.
- smoke.txt: scripts/smoke.py against a fresh `docker compose up` (the private demo, MCP included). 16 checks.
- smoke-public.txt: scripts/smoke_public.py against docker-compose.public.yml (one container in public mode).
  15 checks, two of them new: the landing page and the review app are served with their own security headers.
- screens/: the pages as a visitor of the public rehearsal saw them in Chrome, at 1280 and 375 pixels wide. The
  landing page is also shown in dark mode; the review UI stays light.

Checked by hand in Chrome, not recorded as files: sign-in, queue, a pending, a blocked and a written fix, reject
with a reason, approve and write-back, the products and activity pages, at 375, 768 and 1280 pixels; the browser
console showed no Content-Security-Policy refusals on either page with the real headers. After the review the
/app policy was narrowed from the whole cdn.shopify.com host to its /shopifycloud/ and /static/fonts/ paths, and
these pages were walked through again under it, sign-in by Enter included. The screenshots predate that change; it
alters no pixels.

Review: four pr-review-toolkit agents (code-reviewer, silent-failure-hunter, pr-test-analyzer, comment-analyzer)
read the change. Their findings that were fixed: the CSP allowed every file on cdn.shopify.com, merchant uploads
included; Enter no longer signed in; a click on a disabled approve relied on Polaris alone; a token that expired
mid-session made the next sign-in sign out again at once (an older bug, now covered by a test that fails without the
fix); failed loads showed zero counts or empty tables; a failed refresh replaced the proposal page; an approval's
outcome was not announced after its buttons disappeared; and several wording errors on the landing page and in the
docs. Not done: a CSP report endpoint, which would add an unauthenticated write route.

Not run: a deploy to Render or Neon (no accounts), a live Shopify store, a real model.
