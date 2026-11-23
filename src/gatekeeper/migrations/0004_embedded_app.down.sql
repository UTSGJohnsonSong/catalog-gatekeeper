DROP TABLE IF EXISTS shop_credentials;
ALTER TABLE shops
  DROP COLUMN IF EXISTS redacted_at,
  DROP COLUMN IF EXISTS uninstalled_at,
  DROP COLUMN IF EXISTS installed_at;
