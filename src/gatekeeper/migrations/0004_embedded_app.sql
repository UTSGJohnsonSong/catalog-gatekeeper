-- Embedded app (docs/adr/0013): the store's offline token, encrypted, and the install lifecycle of a store.
ALTER TABLE shops
  ADD COLUMN installed_at timestamptz,
  ADD COLUMN uninstalled_at timestamptz,
  ADD COLUMN redacted_at timestamptz;

-- One row per store, holding ciphertext only; the key is TOKEN_ENCRYPTION_KEY in the environment. The read-only MCP
-- role is granted views only, so it cannot read this table. The token is an expiring offline token: an access token
-- that lasts about an hour and a refresh token that lasts about 90 days. Every column is required, as in TokenGrant.
CREATE TABLE shop_credentials (
  shop_id bigint PRIMARY KEY REFERENCES shops ON DELETE CASCADE,
  access_token bytea NOT NULL,
  access_expires_at timestamptz NOT NULL,
  refresh_token bytea NOT NULL,
  refresh_expires_at timestamptz NOT NULL,
  scope text NOT NULL DEFAULT '',
  -- When this token came from a token exchange, which a reinstall also goes through; a refresh leaves it alone.
  -- A late app/uninstalled from before this moment is about an earlier install.
  exchanged_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
