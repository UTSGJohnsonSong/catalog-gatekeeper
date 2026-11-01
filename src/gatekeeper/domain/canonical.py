"""Canonical forms. Every compare-and-set in the system depends on these staying stable."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any


def canon_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_TAG = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)([^<>]*)>")
_BETWEEN_TAGS = re.compile(r">\s+<")
_SPACE = re.compile(r"\s+")


def html_canon(value: str) -> str:
    """Normalize insignificant HTML differences so a reformatted but identical description hashes the same."""
    text = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n").strip()
    text = _SPACE.sub(" ", text)
    text = _BETWEEN_TAGS.sub("><", text)

    def lower_tag(match: re.Match[str]) -> str:
        slash, name, attrs = match.group(1), match.group(2).lower(), match.group(3)
        if name == "br":
            return "<br>"
        return f"<{slash}{name}{attrs}>"

    return _TAG.sub(lower_tag, text)
