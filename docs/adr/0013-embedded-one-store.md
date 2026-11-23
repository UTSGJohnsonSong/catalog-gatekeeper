# ADR-0013: Embedded in the admin of one store, with session tokens and stored expiring tokens

**Status:** accepted, 2026-11-23.

With `SHOPIFY_EMBEDDED=1` a deployment serves one store and opens inside that store's Shopify admin. Everything follows Shopify's current model for embedded apps, checked against shopify.dev on 2026-11-23:

- **Install.** Managed installation: the scopes live in the app configuration (`shopify.app.toml`), and Shopify installs the app without calling it, so there are no OAuth callback routes.
- **Sign-in.** App Bridge gives the review page a session token per request. The API checks the HS256 signature with the client secret, the audience, that `iss` and `dest` name this store, and the expiry with ten seconds of leeway. A failed check answers 401 with `X-Shopify-Retry-Invalid-Session-Request`, and the page retries once with a new token. Staff of the store act as admins of it. Reviewer tokens still work, for the public demo and for staff outside the admin.
- **Store token.** The first open exchanges the session token for an expiring offline token (`expiring=1`): an access token that lasts an hour and a refresh token that lasts 90 days. Shopify requires these for new public apps since 2026-04-01. Both are stored encrypted with AES-256-GCM. The associated data names the store and the kind of token, so a ciphertext moved to another row or column does not decrypt. The key is `TOKEN_ENCRYPTION_KEY`, and the read-only MCP role cannot read the table.
- **Refresh.** A new token retires the old one, so exchange and refresh for a store hold one Postgres advisory lock, which covers the API and the worker processes, and read the row again after taking it. A refused refresh token deletes the row, and the next open exchanges again. A hiccup at the token endpoint keeps a token that still works and backs off.
- **Framing.** Only the review pages may be framed, and only by `https://{store}` and `https://admin.shopify.com`. The landing page and the API keep `frame-ancestors 'none'`.
- **Privacy webhooks.** `customers/data_request` and `customers/redact` are answered with a record that no customer data is held. Their payloads are not stored, because they name the customer. `app/uninstalled` deletes the token, pauses write-back and drops queued syncs and runs. `shop/redact` deletes the store's catalog and everything derived from it in one transaction. All of them are signed with the client secret.
- **Template drafts.** With the template model and `ALLOW_TEMPLATE_WRITES_TO_DEV_STORE=1`, a write goes out only after Shopify reports `shop.plan.partnerDevelopment` as true. The answer is asked once per process, and anything but `true` refuses the write.

**Why one store per deployment.** Reaching stores that merchants really run needs public distribution, which means App Store review and a one-time registration fee. Without it an app reaches development stores of its own organization, or one chosen store through custom distribution, free and without review. This deployment targets a development store. The schema already keys products, findings, runs and proposals by store. The single-store assumption lives in the wiring: one adapter, one token source and one shop id per process. That is where a multi-store version would start.

**Cost and known limits:**

- The audit log keeps a reviewer's rejection note after `shop/redact`, because the database refuses to change audit rows. In a one-store deployment the reviewer is the merchant's own staff. A later version should store the note with the review, which is deleted, and only its presence in the log.
- No Shopify billing: custom distribution cannot use it.
- Webhook subscriptions are declared in `shopify.app.toml`. Without them the hourly incremental sync still notices product changes, but uninstall and erasure are not seen.
- Rotating `TOKEN_ENCRYPTION_KEY` makes the stored token unreadable, and the merchant reconnects by opening the app once.
