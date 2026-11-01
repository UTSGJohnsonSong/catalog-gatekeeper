# ADR-0011: Bearer reviewer tokens, no cookies

**Status:** accepted.

Approval must not be open, and this is not a multi-tenant product. Tokens are configured as `name:role:sha256`, so only hashes are stored, and compared in constant time. No cookies means no CSRF surface. The name becomes the actor in the audit log.

**Cost:** no SSO, no expiry, no self-service rotation. A public Shopify app would need session tokens instead.
