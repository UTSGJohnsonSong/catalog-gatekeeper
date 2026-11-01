# ADR-0006: Shopify behind a port, and what the fixture does not prove

**Status:** accepted.

`FixtureShopify` and `LiveShopify` implement one `ShopifyPort`, and one parametrized contract suite runs against both. The live adapter runs there against a mocked GraphQL server.

**What this does not prove.** The live adapter has never called a real store. The mock was written from Shopify's documentation, so it shares any misreading of that documentation. Unverified against a real store: whether Shopify normalizes `descriptionHtml` in a way that breaks the read-back, the exact `userErrors` shapes, and real throttling. The first live run should keep writes off, sync, and save real responses as new fixtures.

The fixture's `expected_content_hash` precondition is stricter than Shopify's API, so race tests prove the service handles a stale rejection, not that production closes the race.
