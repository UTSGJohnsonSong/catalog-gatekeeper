# Run it inside a Shopify development store

Embedded mode puts the review screen inside the Shopify admin of one development store. The merchant opens it from the admin, with no token to paste. The design and its limits are in `docs/adr/0013-embedded-one-store.md`.

It costs nothing. It needs:

- a Shopify account for the Dev Dashboard;
- a second free Neon database, next to the demo's;
- a second free Render service from `render.yaml`.

Fixes are drafted by the template script, not an LLM, so no model key is involved. The template may write only to a store that Shopify reports as a development store.

## What it adds

| Piece | Where |
|---|---|
| Session tokens from App Bridge sign the merchant in | `src/gatekeeper/shopify/session_token.py`, `src/gatekeeper/api/auth.py` |
| Token exchange for an expiring offline token, stored encrypted, refreshed under an advisory lock | `src/gatekeeper/shopify/offline_tokens.py`, `src/gatekeeper/shopify/token_crypto.py`, migration `0004` |
| Connecting on first open, uninstall, erasure after `shop/redact` | `src/gatekeeper/services/shop_lifecycle.py` |
| Privacy webhooks, signed with the client secret | `src/gatekeeper/services/webhooks.py` |
| Review page framed only by this store's admin, with App Bridge before Polaris | `src/gatekeeper/api/app.py` |
| Admin sidebar menu, no sign-in page, settings page | `frontend/src/App.tsx`, `frontend/src/pages/Settings.tsx` |
| Template writes only to development stores | `src/gatekeeper/shopify/live.py` |

Tests: `tests/unit/test_embedded_app.py` needs no database. `tests/integration/test_embedded_app.py` runs against real Postgres and covers the refresh race (two token sources on separate connections, standing in for the API and the worker), uninstall and reinstall, erasure, forged and replayed webhooks, and sign-in. `frontend/src/embedded.test.tsx` covers the page.

## Steps

1. **Store and app.** At https://dev.shopify.com/dashboard/ create a development store, then an app in the same organization. A store made from a normal Shopify admin is not in the organization.
2. **Database.** In the Neon project of the demo, add a database, for example `gatekeeper_live`. Copy its direct connection string, not the `-pooler` one. The demo refuses, at its next boot or reset, a database that holds a store token; the app itself does not check, so keep the two connection strings apart.
3. **Token key.** Make 32 random bytes in base64, and keep the result only in Render:
   ```powershell
   $b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); [Convert]::ToBase64String($b)
   ```
   or `python -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"`.
4. **Service.** Create the Render Blueprint from `render.yaml`. For `catalog-gatekeeper-app`, enter the database URL, the store's `myshopify.com` domain, the client ID, the client secret and the token key. The first boot skips its first sync, because no store token exists yet.
5. **App configuration.** Fill in the two placeholders in `shopify.app.toml` with the client ID and the service's host. Then either run `shopify app deploy`, or create a version in the Dev Dashboard with the same values and release it:
   - app URL `https://<host>/app/`, embedded;
   - scopes `read_products,write_products`;
   - webhooks API version `2026-10`;
   - webhooks at `https://<host>/webhooks/shopify`: `app/uninstalled`, `products/create`, `products/update` and `products/delete`, and the three privacy webhooks `customers/data_request`, `customers/redact` and `shop/redact`.
6. **Products, before the first open.** `python scripts/devstore_csv.py` writes `build/dev-store-products.csv`. Import it in the store admin under Products, then Import. Three products arrive, carrying defects A02, A07 and A13.
7. **Install and open.** Install the app on the store and open it from the admin's Apps menu. Opening it once connects the store and starts the first sync: the page says it is reading the catalog, the Activity page records `shop.connected`, and the three products appear on the Products page with their findings. Later changes arrive by webhook, or with the hourly sync. Optionally, `python scripts/capture_live_products.py` then saves Shopify's raw answer, with the store's domain redacted, and audits it.
8. **One fix.** On the review queue, choose "Audit more products", open a fix, read the checks, and approve it. The worker writes it, reads the product back and compares hashes before it shows "Written". Check the product in the admin.
9. **Stop.** Pause writing on the Settings page. Uninstalling the app deletes the store token at once. Shopify sends `shop/redact` 48 hours later, and the app then erases the store's catalog.

## When something goes wrong

- `shop_not_permitted`, or no install button: the store is not in the app's organization. Create it in the Dev Dashboard.
- The page stays at "Connecting to your store": the browser console shows whether App Bridge loaded. The meta tag with the client ID must precede `app-bridge.js`, and the server adds both only with `SHOPIFY_EMBEDDED=1`.
- A blank frame: the page's `frame-ancestors` names `SHOPIFY_SHOP_DOMAIN`. It must be the store you opened.
- A fix ends as "Write failed" with `writes_not_allowed`: Shopify did not report the store as a development store. Whether a store made in the Dev Dashboard reports `partnerDevelopment: true` has not been checked against a real store yet.
- Since October 2026 Shopify verifies the developer's identity before an app is installed on a development store (https://shopify.dev/changelog/blog/app-security-updates-october-2026). The page gives no steps.
