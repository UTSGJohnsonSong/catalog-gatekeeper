"""Store tokens are encrypted at rest with AES-256-GCM.

The associated data names the store and the kind of token, so a ciphertext copied onto another row, or swapped
between the access and refresh columns, fails to decrypt instead of being used for the wrong purpose.
"""

from __future__ import annotations

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

FORMAT_VERSION = b"\x01"
NONCE_BYTES = 12
TAG_BYTES = 16


class TokenDecryptError(Exception):
    """The ciphertext was made with another key, for another row, or was changed."""


class TokenCipher:
    def __init__(self, key: bytes):
        if len(key) != 32:
            raise ValueError("the token key must be 32 bytes")
        self._aead = AESGCM(key)

    def __repr__(self) -> str:
        return "TokenCipher(***)"

    def encrypt(self, plaintext: str, *, context: str) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        return FORMAT_VERSION + nonce + self._aead.encrypt(nonce, plaintext.encode("utf-8"), context.encode("utf-8"))

    def decrypt(self, blob: bytes, *, context: str) -> str:
        if len(blob) < 1 + NONCE_BYTES + TAG_BYTES or blob[:1] != FORMAT_VERSION:
            raise TokenDecryptError("unknown ciphertext format")
        nonce, sealed = blob[1 : 1 + NONCE_BYTES], blob[1 + NONCE_BYTES :]
        try:
            return self._aead.decrypt(nonce, sealed, context.encode("utf-8")).decode("utf-8")
        except InvalidTag as exc:
            raise TokenDecryptError("the token does not decrypt with this key for this row") from exc
