"""Fencing for untrusted catalog text. The fence is a hint to the model; the real defense is structural
(narrow tools, deterministic validators, a human approving an exact diff)."""

from __future__ import annotations

import re
import secrets
import unicodedata

from gatekeeper.rules.text import HIDDEN_CHARS

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CLOSE = re.compile(r"</\s*untrusted", re.IGNORECASE)
_FENCED = re.compile(r'^<untrusted_data field="[^"]*" nonce="[0-9a-f]+">(.*)</untrusted_data>$', re.DOTALL)


def new_nonce() -> str:
    return secrets.token_hex(8)


def sanitize(text: str) -> tuple[str, bool]:
    cleaned = _CONTROL.sub("", HIDDEN_CHARS.sub("", unicodedata.normalize("NFC", text)))
    cleaned = _CLOSE.sub(r"<\\/untrusted", cleaned)
    return cleaned, cleaned != text


def fence(field: str, text: str, nonce: str) -> str:
    cleaned, _ = sanitize(text)
    return f'<untrusted_data field="{field}" nonce="{nonce}">{cleaned}</untrusted_data>'


def unfence(value: str) -> str:
    match = _FENCED.match(value)
    return match.group(1) if match else value
