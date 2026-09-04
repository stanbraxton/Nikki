"""Symmetric encryption for stored credentials (OAuth refresh tokens, API keys, provider
client secrets). Key = TOKEN_ENCRYPTION_KEY (urlsafe base64, 32 bytes) or, if unset,
derived from CHAINLIT_AUTH_SECRET so no extra secret is required to get started."""
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

_f: Fernet | None = None


def _fernet() -> Fernet:
    global _f
    if _f is None:
        key = (os.environ.get("TOKEN_ENCRYPTION_KEY") or "").strip()
        if not key:
            seed = (os.environ.get("CHAINLIT_AUTH_SECRET") or "nikki-dev").encode()
            key = base64.urlsafe_b64encode(hashlib.sha256(b"nikki-token-key:" + seed).digest()).decode()
        _f = Fernet(key.encode())
    return _f


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise RuntimeError("stored credential cannot be decrypted (encryption key changed?)") from e
