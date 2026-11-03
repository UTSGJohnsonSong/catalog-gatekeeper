"""Bearer reviewer tokens. No cookies, so there is no CSRF surface. Only SHA-256 hashes are configured."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from gatekeeper.config import Settings

_bearer = HTTPBearer(auto_error=False)
DEMO_TOKENS = {"demo-reviewer": ("demo_reviewer", "reviewer"), "demo-admin": ("demo_admin", "admin")}


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


def current_principal(
    request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)
) -> Principal:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "Missing or invalid bearer token", {"WWW-Authenticate": "Bearer"}
    )
    if credentials is None:
        raise unauthorized
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
