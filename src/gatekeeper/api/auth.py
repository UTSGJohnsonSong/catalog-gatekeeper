"""Bearer tokens. No cookies, so there is no CSRF surface.

Two kinds: reviewer tokens, of which only SHA-256 hashes are configured, and, in embedded mode, the App Bridge
session token that Shopify gives the review app inside the admin. Staff of the one store the deployment serves act
as admins of it."""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from gatekeeper.config import Settings
from gatekeeper.shopify import session_token
from gatekeeper.shopify.session_token import SessionToken

log = logging.getLogger("gatekeeper.api")
_bearer = HTTPBearer(auto_error=False)
DEMO_TOKENS = {"demo-reviewer": ("demo_reviewer", "reviewer"), "demo-admin": ("demo_admin", "admin")}
# App Bridge fetches a new session token and retries once when a 401 carries this header.
RETRY_SESSION_HEADER = "X-Shopify-Retry-Invalid-Session-Request"


@dataclass(frozen=True)
class Principal:
    name: str
    role: str


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_tokens(settings: Settings) -> list[tuple[str, str, str]]:
    """Returns (sha256, name, role). Demo tokens exist only with DEMO_MODE on and nothing configured.

    A public demo is the exception: it always accepts the reviewer demo token, next to any configured tokens, and
    never the admin one, because the sign-in page shows the demo token to every visitor."""
    raw = settings.reviewer_tokens.get_secret_value().strip() if settings.reviewer_tokens else ""
    entries: list[tuple[str, str, str]] = []
    for item in filter(None, (part.strip() for part in raw.split(","))):
        parts = item.split(":")
        if len(parts) != 3 or parts[1] not in ("reviewer", "admin") or len(parts[2]) != 64:
            raise ValueError("REVIEWER_TOKENS entries must look like name:role:sha256hex")
        entries.append((parts[2].lower(), parts[0], parts[1]))
    if settings.public_demo:
        name, role = DEMO_TOKENS["demo-reviewer"]
        entries.append((_sha256("demo-reviewer"), name, role))
    elif not entries and settings.demo_mode:
        entries = [(_sha256(token), name, role) for token, (name, role) in DEMO_TOKENS.items()]
    return entries


def demo_token(settings: Settings) -> str | None:
    """The token the sign-in page may fill in, or None when no demo token is accepted."""
    if settings.public_demo:
        return "demo-reviewer"
    configured = settings.reviewer_tokens and settings.reviewer_tokens.get_secret_value().strip()
    if settings.demo_mode and not configured:
        return "demo-admin"
    return None


def session_rejected(detail: str = "Session token missing or invalid", *, retry: bool = False) -> HTTPException:
    headers = {"WWW-Authenticate": "Bearer"}
    if retry:
        headers[RETRY_SESSION_HEADER] = "1"
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers)


def _verify_session(settings: Settings, raw: str) -> SessionToken:
    if not (settings.shopify_client_id and settings.shopify_client_secret):
        raise session_rejected()  # Unreachable with a valid config: embedded mode requires both.
    try:
        return session_token.verify(
            raw,
            client_id=settings.shopify_client_id,
            client_secret=settings.shopify_client_secret.get_secret_value(),
            shop_domain=settings.shopify_shop_domain,
        )
    except session_token.InvalidSessionToken as exc:
        if exc.retryable:
            log.info("session token outside its time window: %s", exc)
            raise session_rejected(retry=True) from exc
        # A wrong setting or a forgery. Warning, not error: anyone can send a bad token.
        log.warning(
            "session token refused (%s); if this repeats, check SHOPIFY_CLIENT_ID, SHOPIFY_CLIENT_SECRET and "
            "SHOPIFY_SHOP_DOMAIN",
            exc,
        )
        raise session_rejected() from exc


def current_session(
    request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)
) -> SessionToken:
    """Only a session token, for the call that connects the store."""
    settings: Settings = request.app.state.settings
    if not settings.shopify_embedded:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This deployment does not run inside the Shopify admin")
    if credentials is None:
        raise session_rejected()
    if not session_token.looks_like_session_token(credentials.credentials):
        raise session_rejected()
    return _verify_session(settings, credentials.credentials)


def current_principal(
    request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)
) -> Principal:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "Missing or invalid bearer token", {"WWW-Authenticate": "Bearer"}
    )
    if credentials is None:
        raise unauthorized
    settings: Settings = request.app.state.settings
    if settings.shopify_embedded and session_token.looks_like_session_token(credentials.credentials):
        session = _verify_session(settings, credentials.credentials)
        return Principal(f"shopify:{session.user_id}", "admin")
    presented = _sha256(credentials.credentials)
    match = None
    for stored, name, role in request.app.state.tokens:
        if hmac.compare_digest(stored, presented):
            match = Principal(name, role)
    if match is None:
        raise unauthorized
    return match


def require_admin(principal: Principal = Depends(current_principal)) -> Principal:
    if principal.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required")
    return principal
