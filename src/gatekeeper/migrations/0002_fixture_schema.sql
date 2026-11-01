-- The fixture store stands in for a Shopify shop in demos and tests. Separate schema: the app's own
-- tables never read it directly, only through the ShopifyPort.
CREATE SCHEMA fixture;
CREATE TABLE fixture.products (
  gid text PRIMARY KEY,
  data jsonb NOT NULL,
  version int NOT NULL DEFAULT 1,
  updated_at timestamptz NOT NULL DEFAULT now()
);
