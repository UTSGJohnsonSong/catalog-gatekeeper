# ADR-0005: A webhook is only a signal

**Status:** accepted.

The route verifies HMAC on raw bytes before parsing, inserts on a unique `X-Shopify-Webhook-Id`, enqueues, and returns. The job takes only a product id from the payload and refetches the store. So delivery order does not matter, and a payload cannot plant data. An hourly incremental sync catches missed deliveries.

**Known weakness:** the id headers are outside the HMAC. A captured signed body replayed with a fresh id skips dedupe. The effect is one extra refetch and no state change, which a test covers. Signal-only processing is what makes this harmless.

**Out of scope:** Shopify's mandatory compliance webhooks. This is a single-merchant app that stores no customer data, and a public app would need them.
