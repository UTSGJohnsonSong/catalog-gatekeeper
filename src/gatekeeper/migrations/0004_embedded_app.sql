-- Embedded app (docs/adr/0013): the store's offline token, encrypted, and the install lifecycle of a store.
ALTER TABLE shops
  ADD COLUMN installed_at timestamptz,
  ADD COLUMN uninstalled_at timestamptz,
  ADD COLUMN redacted_at timestamptz;

-- One row per store, holding ciphertext only; the key is TOKEN_ENCRYPTION_KEY in the environment. The read-only MCP
-- role is granted views only, so it cannot read this table. An expiring offline token has an access token that
-- lasts an hour and a refresh token that lasts 90 days; a non-expiring one has neither expiry nor refresh token.
CREATE TABLE shop_credentials (
  shop_id bigint PRIMARY KEY REFERENCES shops ON DELETE CASCADE,
  access_token bytea NOT NULL,
  access_expires_at timestamptz,
  refresh_token bytea,
  refresh_expires_at timestamptz,
  scope text NOT NULL DEFAULT '',
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK ((refresh_token IS NULL) = (refresh_expires_at IS NULL)),
  CHECK ((refresh_token IS NULL) = (access_expires_at IS NULL))
);
