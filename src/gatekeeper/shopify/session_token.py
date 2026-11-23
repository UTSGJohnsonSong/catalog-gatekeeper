"""App Bridge session tokens: the ID token Shopify hands an embedded app's frontend, checked on every request.

Shopify signs them with HS256 and the app's client secret. They last one minute, so none is cached or stored, and
the store always comes from the token's `dest` claim, never from a query parameter.
https://shopify.dev/docs/apps/build/authentication-authorization/session-tokens
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

import jwt

# Shopify's own Node library allows ten seconds of clock skew between its servers and the app's.
LEEWAY_SECONDS = 10
REQUIRED_CLAIMS = ["exp", "nbf", "iss", "dest", "aud", "sub"]


class InvalidSessionToken(Exception):
    """The token is not one Shopify issued to this app's frontend for this store.

    `retryable` is true only for a token outside its one-minute window, which a fresh token from App Bridge fixes. A
    bad signature, audience or store means a wrong setting, or a forgery, and a retry cannot help."""

    def __init__(self, reason: str, *, retryable: bool = False):
        super().__init__(reason)
        self.retryable = retryable


@dataclass(frozen=True)
class SessionToken:
    shop_domain: str
    user_id: str
    raw: str

    def __repr__(self) -> str:
        return f"SessionToken(shop_domain={self.shop_domain!r}, user_id={self.user_id!r}, raw=***)"


def looks_like_session_token(value: str) -> bool:
    """A JWT has three dot-separated parts. In embedded mode a bearer token with exactly two dots is read as a
    session token, so reviewer tokens should contain no dots."""
    return value.count(".") == 2


def verify(token: str, *, client_id: str, client_secret: str, shop_domain: str) -> SessionToken:
    try:
        claims = jwt.decode(
            token,
            client_secret,
            algorithms=["HS256"],  # Fixed here, so a token cannot choose a weaker algorithm or none.
            audience=client_id,
            leeway=LEEWAY_SECONDS,
            options={"require": REQUIRED_CLAIMS},
        )
    except (jwt.ExpiredSignatureError, jwt.ImmatureSignatureError) as exc:
        raise InvalidSessionToken(type(exc).__name__, retryable=True) from exc
    except jwt.PyJWTError as exc:
        # The library's message can quote claims, so only the error class is kept.
        raise InvalidSessionToken(type(exc).__name__) from exc
    dest, issuer = _https_host(claims.get("dest")), _https_host(claims.get("iss"))
    if dest is None or dest != issuer:
        raise InvalidSessionToken("iss and dest do not name the same store")
    if dest != shop_domain:
        raise InvalidSessionToken("the token was issued for another store")
    user = claims.get("sub")
    if not isinstance(user, str) or not user.strip():
        raise InvalidSessionToken("the token names no user")
    return SessionToken(shop_domain=dest, user_id=user, raw=token)


def _https_host(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname:
        return None
    return parts.hostname.lower()
