# Steps only the owner can do

Everything in the README runs without these. Each one needs an account, a key or spending approval.

**1. Make the repo public.** Start the `ci` workflow by hand first, because only that run makes gitleaks scan the whole history.

**2. Measure the agent with a real model (spends money; ran once, repeat it after changes).** Create an API key with the hosted LLM provider and set a spend limit in its console. That limit is the real cap: `DAILY_COST_CAP_USD` (default 5 USD) counts only the current run, because each run starts with an empty eval database. One run makes 40 agent runs, each capped at 0.25 USD.

Start only the database, and set the key only in this shell. A key visible to a full `docker compose up` switches the demo to the hosted LLM, and it runs up to 16 products on its own.
```bash
docker compose up -d db
export ANTHROPIC_API_KEY=...   # PowerShell: $env:ANTHROPIC_API_KEY="..."
export DAILY_COST_CAP_USD=1    # PowerShell: $env:DAILY_COST_CAP_USD="1"
python -m gatekeeper_eval agent --split dev
```
Use the virtual environment from the README's "Tests" section. The sweep stops after 3 runs in a row raise an error, so a bad key costs nothing. Status `FAILED` means no run finished, and `PARTIAL` means it stopped early. Do not quote either. If the first run reaches the 1 USD cap, raise the cap and run again.

The report in `eval/reports/` gives run outcomes, validator results, judge concerns, policy retrieval, citation checks, cost and latency. It does not score repair quality, so read `per_case`, above all the 10 injection cases. `injected_phrase_copied` only detects verbatim copying.

**3. Run it inside a real development store (free).** `docs/EMBEDDED.md` has the steps: a development store and an app in the Dev Dashboard, a second Neon database, the `catalog-gatekeeper-app` service from `render.yaml`, and the app configuration in `shopify.app.toml`. The merchant then opens the review screen from the store's admin, and the first open connects the store. Fixes are drafted by the template script, which may write only to a store that Shopify reports as a development store. Without embedded mode, the live adapter can also run from a shell with the app's client ID and secret, which get a 24-hour token by the client credentials grant; keep writes off until real responses match the mock in `tests/integration/test_shopify_adapters.py`.

**4. Put the demo online (free, two free accounts).** Create a Neon project and a Render Blueprint from `render.yaml`. Nothing is billed: the service sleeps when idle, and running out of free hours suspends it until next month. Steps, limits and what a visitor can do are in `docs/DEPLOY_DEMO.md`. Rehearse first with `docker compose -f docker-compose.public.yml up -d --build --wait` and `python scripts/smoke_public.py`.

**5. Deploy to AWS (spends money every month, about one small RDS instance, one load balancer and two Fargate tasks).** The HTTPS listener needs an ACM certificate, and that needs a domain you control. The tasks take the app's client ID as a variable and its client secret from Secrets Manager, so the token renews itself. Create an S3 state bucket and the certificate, then:
```bash
cd infra/terraform
terraform init -backend-config="bucket=YOUR_BUCKET" -backend-config="key=gatekeeper.tfstate" -backend-config="region=ca-central-1"
terraform plan -var image_tag=v1 -var acm_certificate_arn=YOUR_ARN -var shop_domain=your-store.myshopify.com -var shopify_client_id=YOUR_CLIENT_ID
```
Read the plan, then apply it. Push the image to the ECR repository it creates, set each secret with `aws secretsmanager put-secret-value`, and run the `migrate` task once before starting the services. `WRITEBACK_ENABLED` stays off until you change the variable.
