"""Expiring offline tokens for the embedded app, kept in Postgres and encrypted. See docs/adr/0013.

Managed installation installs the app without calling it. The first time a merchant opens the app, the frontend's
session token is exchanged for an offline token. Offline tokens now expire after an hour and come with a refresh
token that lasts 90 days; a refresh returns a new pair.

Shopify keeps one current token per app and store, and a new exchange or refresh retires the older ones. So exchange
and refresh for a store never run at the same time: both hold a Postgres advisory lock for the store, which covers
the API and the worker processes alike, and both read the stored row again after taking it.

https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens
https://shopify.dev/docs/apps/build/authentication-authorization/implement-token-exchange
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from gatekeeper.config import MYSHOPIFY_DOMAIN
from gatekeeper.db import Conn, tx
from gatekeeper.shopify.port import ShopifyAuthError, ShopifyThrottled, ShopifyTransient
from gatekeeper.shopify.token_crypto import TokenCipher, TokenDecryptError

log = logging.getLogger("gatekeeper.shopify")

# Protocol names from RFC 8693 and Shopify's docs, not secrets.
TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"  # noqa: S105
ID_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:id_token"  # noqa: S105
OFFLINE_TOKEN_TYPE = "urn:shopify:params:oauth:token-type:offline-access-token"  # noqa: S105
LOCK_NAMESPACE = 727_003  # migrate.py holds 727_001, the demo reset in seed.py 727_002
REFRESH_MARGIN = timedelta(minutes=5)
# After a failed early renewal, the next attempt waits this long, or as long as Retry-After says.
RENEWAL_BACKOFF_SECONDS = 30.0
ACTOR = "shopify-auth"
# OAuth error codes that mean the refresh token itself is dead. Anything else, invalid_client above all, points at the
# app's own configuration, and a refresh token that may still be good is kept.
DEAD_REFRESH_TOKEN_CODES = {"invalid_request", "invalid_grant"}


def utc_now() -> datetime:
    return datetime.now(UTC)


class ShopNotConnected(ShopifyAuthError):
    """No usable token is stored. The merchant reconnects the store by opening the app in the Shopify admin."""


class TokenUnreadable(ShopNotConnected):
    """A token is stored but does not decrypt, usually because TOKEN_ENCRYPTION_KEY changed."""


class RefreshRejected(ShopifyAuthError):
    """Shopify refused the refresh token. That is final: only a new token exchange restores access."""


class SessionTokenRejected(Exception):
    """Shopify refused the session token in a token exchange, most often because it had just expired."""


@dataclass(frozen=True)
class TokenGrant:
    """An expiring offline token: an access token for about an hour and a refresh token for about 90 days.

    Shopify requires expiring tokens for new public apps, and this app always asks for one (`expiring=1`), so a token
    that does not expire is refused where the answer is parsed. The table's NOT NULL columns hold the same shape."""

    access_token: str
    scope: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime

    def __post_init__(self) -> None:
        if not self.access_token or not self.refresh_token:
            raise ValueError("a token grant needs both an access token and a refresh token")
        if self.access_expires_at.tzinfo is None or self.refresh_expires_at.tzinfo is None:
            raise ValueError("token expiry times must carry a time zone")

    def __repr__(self) -> str:
        return f"TokenGrant(scope={self.scope!r}, access_expires_at={self.access_expires_at!r}, tokens=***)"

    def access_usable(self, now: datetime, margin: timedelta = timedelta(0)) -> bool:
        return now < self.access_expires_at - margin

    def refresh_usable(self, now: datetime) -> bool:
        return now < self.refresh_expires_at


def _error_code(response: httpx.Response) -> str | None:
    """The OAuth `error` code, if it is a plain code. Nothing else from an error body is ever repeated."""
    try:
        body = response.json()
    except ValueError:
        return None
    code = body.get("error") if isinstance(body, dict) else None
    return code if isinstance(code, str) and code.replace("_", "").isalpha() and len(code) <= 40 else None


def _seconds(value: Any) -> float | None:
    """A lifetime in seconds, or None when the field is missing, not a number, or not positive."""
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    return float(value)


class TokenEndpoint:
    """The store's /admin/oauth/access_token. Error bodies are never logged or raised, because they can echo the
    request, and the request carries the client secret."""

    def __init__(
        self,
        *,
        shop_domain: str,
        client_id: str,
        client_secret: str,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] = utc_now,
    ):
        if not MYSHOPIFY_DOMAIN.fullmatch(shop_domain):
            # The client secret is posted to this host, so it must be a store's own Shopify domain.
            raise ValueError("shop_domain must be the store's myshopify.com domain")
        self._url = f"https://{shop_domain}/admin/oauth/access_token"
        self._client_id = client_id
        self._client_secret = client_secret
        self._clock = clock
        self._http = httpx.Client(timeout=15.0, transport=transport)

    def __repr__(self) -> str:
        return f"TokenEndpoint(url={self._url!r}, client_id={self._client_id!r}, secret=***)"

    def exchange(self, session_token: str) -> TokenGrant:
        response, started = self._request(
            {
                "grant_type": TOKEN_EXCHANGE_GRANT,
                "subject_token": session_token,
                "subject_token_type": ID_TOKEN_TYPE,
                "requested_token_type": OFFLINE_TOKEN_TYPE,
                "expiring": "1",
            },
            purpose="token exchange",
        )
        if response.status_code == 400:
            raise SessionTokenRejected("Shopify rejected the session token")
        return self._grant(response, started, purpose="token exchange")

    def refresh(self, refresh_token: str) -> TokenGrant:
        response, started = self._request(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}, purpose="token refresh"
        )
        if response.status_code in (400, 401):
            code = _error_code(response)
            if code in DEAD_REFRESH_TOKEN_CODES:
                raise RefreshRejected(f"Shopify refused the refresh token (HTTP {response.status_code}, {code})")
            raise ShopifyAuthError(
                f"token refresh rejected with HTTP {response.status_code} ({code or 'no error code'}); "
                "check SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET"
            )
        return self._grant(response, started, purpose="token refresh")

    def _request(self, fields: dict[str, str], *, purpose: str) -> tuple[httpx.Response, datetime]:
        started = self._clock()  # Lifetimes count from before the request, so a slow answer cannot overstate them.
        data = {"client_id": self._client_id, "client_secret": self._client_secret, **fields}
        try:
            response = self._http.post(self._url, data=data, headers={"Accept": "application/json"})
        except httpx.TimeoutException as exc:
            raise ShopifyTransient(f"timeout during {purpose}") from exc
        except httpx.TransportError as exc:
            raise ShopifyTransient(f"transport error during {purpose}: {type(exc).__name__}") from exc
        if response.status_code == 429:
            raise ShopifyThrottled(float(response.headers.get("Retry-After", "2")))
        if response.status_code >= 500:
            raise ShopifyTransient(f"{response.status_code} from the token endpoint during {purpose}")
        return response, started

    @staticmethod
    def _grant(response: httpx.Response, started: datetime, *, purpose: str) -> TokenGrant:
        if response.status_code != 200:
            raise ShopifyAuthError(f"{purpose} rejected with HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise ShopifyTransient(f"{purpose} returned a non-JSON body") from exc
        if not isinstance(body, dict):
            raise ShopifyAuthError(f"{purpose} returned an unexpected body")
        access = body.get("access_token")
        if not isinstance(access, str) or not access:
            raise ShopifyAuthError(f"{purpose} returned no access_token")
        raw_scope = body.get("scope")
        scope = raw_scope if isinstance(raw_scope, str) else ""
        refresh = body.get("refresh_token")
        lifetime, refresh_lifetime = _seconds(body.get("expires_in")), _seconds(body.get("refresh_token_expires_in"))
        if lifetime is None or refresh_lifetime is None or not isinstance(refresh, str) or not refresh:
            # This app always asks for an expiring token, so anything else is an answer it does not understand.
            raise ShopifyAuthError(f"{purpose} returned a token that does not expire, or an incomplete one")
        return TokenGrant(
            access,
            scope,
            started + timedelta(seconds=lifetime),
            refresh,
            started + timedelta(seconds=refresh_lifetime),
        )


@dataclass(frozen=True)
class StoreAuth:
    """What the embedded app needs to get and keep a store token."""

    endpoint: TokenEndpoint
    cipher: TokenCipher


# Storage -----------------------------------------------------------------------------------------------------------


def lock_store_tokens(conn: Conn, shop_id: int) -> None:
    """Held until the transaction ends. Exchange and refresh for one store take turns behind it."""
    conn.execute("SELECT pg_advisory_xact_lock(%s, %s)", (LOCK_NAMESPACE, shop_id))


def _context(shop_domain: str, kind: str) -> str:
    return f"shop_credentials:{shop_domain}:{kind}"


def load_grant(conn: Conn, shop_id: int, shop_domain: str, cipher: TokenCipher) -> TokenGrant | None:
    row = conn.execute(
        "SELECT access_token, access_expires_at, refresh_token, refresh_expires_at, scope "
        "FROM shop_credentials WHERE shop_id = %s",
        (shop_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        access = cipher.decrypt(bytes(row["access_token"]), context=_context(shop_domain, "access"))
        refresh = cipher.decrypt(bytes(row["refresh_token"]), context=_context(shop_domain, "refresh"))
    except TokenDecryptError as exc:
        # A changed TOKEN_ENCRYPTION_KEY lands here. The next app open exchanges a new token and overwrites the row.
        raise TokenUnreadable(
            "the stored store token does not decrypt with TOKEN_ENCRYPTION_KEY; open the app from the Shopify admin"
        ) from exc
    return TokenGrant(access, row["scope"], row["access_expires_at"], refresh, row["refresh_expires_at"])


def save_grant(conn: Conn, shop_id: int, shop_domain: str, cipher: TokenCipher, grant: TokenGrant) -> None:
    conn.execute(
        "INSERT INTO shop_credentials "
        "(shop_id, access_token, access_expires_at, refresh_token, refresh_expires_at, scope, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, now()) ON CONFLICT (shop_id) DO UPDATE SET "
        "access_token = EXCLUDED.access_token, access_expires_at = EXCLUDED.access_expires_at, "
        "refresh_token = EXCLUDED.refresh_token, refresh_expires_at = EXCLUDED.refresh_expires_at, "
        "scope = EXCLUDED.scope, updated_at = now()",
        (
            shop_id,
            cipher.encrypt(grant.access_token, context=_context(shop_domain, "access")),
            grant.access_expires_at,
            cipher.encrypt(grant.refresh_token, context=_context(shop_domain, "refresh")),
            grant.refresh_expires_at,
            grant.scope,
        ),
    )


def delete_grant(conn: Conn, shop_id: int) -> bool:
    return conn.execute("DELETE FROM shop_credentials WHERE shop_id = %s", (shop_id,)).rowcount > 0


def _disconnect(conn: Conn, shop_id: int, reason: str) -> None:
    """Forgets a token that can never work again, and says so in the log and the audit log."""
    delete_grant(conn, shop_id)
    log.error("store disconnected: %s; open the app from the Shopify admin to reconnect", reason)
    conn.execute(
        "INSERT INTO audit_log (actor, action, entity_type, entity_id, shop_id, detail) "
        "VALUES (%s, 'shop.disconnected', 'shop', %s, %s, %s)",
        (ACTOR, str(shop_id), shop_id, Jsonb({"reason": reason})),
    )


# The token source the live adapter uses ---------------------------------------------------------------------------


class StoredOfflineToken:
    """A TokenSource for LiveShopify that reads the stored token and refreshes it shortly before it expires.

    One instance is shared by every thread of a process; the lock makes concurrent callers wait for one refresh.
    Other processes are kept in step by the advisory lock and by reading the row again under it."""

    refreshable = True

    def __init__(
        self,
        *,
        pool: ConnectionPool[Conn],
        shop_id: int,
        shop_domain: str,
        auth: StoreAuth,
        clock: Callable[[], datetime] = utc_now,
        margin: timedelta = REFRESH_MARGIN,
    ):
        self._pool = pool
        self._shop_id = shop_id
        self._shop_domain = shop_domain
        self._auth = auth
        self._clock = clock
        self._margin = margin
        self._lock = threading.Lock()
        self._cached: TokenGrant | None = None
        self._rejected: str | None = None  # an access token the Admin API answered 401 to
        self._renew_after = 0.0  # monotonic time before which a failed early renewal is not retried

    def __repr__(self) -> str:
        return f"StoredOfflineToken(shop={self._shop_domain!r}, tokens=***)"

    def get(self) -> str:
        with self._lock:
            now = self._clock()
            cached = self._cached
            if cached is not None and cached.access_token != self._rejected:
                if cached.access_usable(now, self._margin):
                    return cached.access_token
                if cached.access_usable(now) and time.monotonic() < self._renew_after:
                    return cached.access_token  # Backing off after a failed renewal, while the token still works.
            grant = self._load_or_refresh(now)
            self._cached = grant
            return grant.access_token

    def invalidate(self, token: str) -> None:
        """Mark a token the store rejected, so the next call refreshes instead of sending it again."""
        with self._lock:
            self._rejected = token
            if self._cached is not None and self._cached.access_token == token:
                self._cached = None

    def _load_or_refresh(self, now: datetime) -> TokenGrant:
        failure: ShopNotConnected | None = None
        with tx(self._pool, ACTOR) as conn:
            lock_store_tokens(conn, self._shop_id)
            grant = load_grant(conn, self._shop_id, self._shop_domain, self._auth.cipher)
            if grant is None:
                raise ShopNotConnected("the app is not connected to this store; open it from the Shopify admin")
            rejected = grant.access_token == self._rejected
            if not rejected and grant.access_usable(now, self._margin):
                return grant  # Still fresh, or another process refreshed it already.
            if not grant.refresh_usable(now):
                _disconnect(conn, self._shop_id, "the refresh token expired")
                failure = ShopNotConnected("the refresh token expired; open the app from the Shopify admin")
            else:
                try:
                    renewed = self._auth.endpoint.refresh(grant.refresh_token)
                except RefreshRejected as exc:
                    _disconnect(conn, self._shop_id, str(exc))
                    failure = ShopNotConnected("Shopify refused the refresh token; open the app from the Shopify admin")
                    failure.__cause__ = exc
                except (ShopifyTransient, ShopifyThrottled) as exc:
                    # Renewing early is a courtesy. While the current token still works, a hiccup at the token
                    # endpoint must not fail a call that would have worked, nor be retried on every call.
                    if rejected or not grant.access_usable(now):
                        raise
                    pause = exc.retry_after if isinstance(exc, ShopifyThrottled) else RENEWAL_BACKOFF_SECONDS
                    self._renew_after = time.monotonic() + max(1.0, pause)
                    log.warning("could not refresh the store token (%s); the current one still works", exc)
                    return grant
                else:
                    save_grant(conn, self._shop_id, self._shop_domain, self._auth.cipher, renewed)
                    self._rejected = None
                    return renewed
        # Raised after the block, so the deletion above is committed and a second caller does not retry it.
        raise failure or ShopNotConnected("the app is not connected to this store; open it from the Shopify admin")
