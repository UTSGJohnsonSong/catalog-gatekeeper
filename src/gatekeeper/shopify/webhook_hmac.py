"""Shopify webhook signature: base64(HMAC-SHA256(client secret, raw body))."""

from __future__ import annotations

import base64
import hashlib
import hmac


def sign(secret: str, raw_body: bytes) -> str:
    return base64.b64encode(hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()).decode("ascii")


def verify(secret: str, raw_body: bytes, header_value: str | None) -> bool:
    if not header_value:
        return False
    return hmac.compare_digest(sign(secret, raw_body).encode("ascii"), header_value.strip().encode("utf-8", "replace"))
