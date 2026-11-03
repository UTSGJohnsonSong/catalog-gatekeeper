Checks run on 2026-11-03 for the free public demo, the demo reset and Shopify token renewal.

How the containers ran: the proxy this machine uses to pull images was off, so the image could not be rebuilt.
Both stacks ran the existing catalog-gatekeeper:local image with this tree's src/, fixtures/ and a fresh frontend
build mounted read-only over the copies baked into it. Python dependencies did not change, so the code exercised is
the code in this commit. The CI demo-smoke job builds the image from scratch.

Files:
- pytest.txt: the full backend suite against real Postgres, with coverage.
- frontend.txt: type check, unit tests, production build.
- smoke.txt: scripts/smoke.py against `docker compose up` (the private demo, MCP included).
- smoke-public.txt: scripts/smoke_public.py against docker-compose.public.yml (one container in public mode).
- scheduled-reset.txt: the public container's log around one scheduled reset, with the interval set to 5 minutes.
- terraform.txt: fmt and validate. Nothing was planned or applied.

Not run: a deploy to Render or Neon (no accounts), a live Shopify store, a real model.
