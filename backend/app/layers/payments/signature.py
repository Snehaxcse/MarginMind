"""HMAC-SHA256 webhook signatures. Always verify the raw request body bytes."""

from __future__ import annotations

import hashlib
import hmac


def sha256_hex(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def hmac_sha256_hex(*, secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def signatures_match(*, secret: str, body: bytes, signature: str) -> bool:
    if not secret or not signature:
        return False
    expected = hmac_sha256_hex(secret=secret, body=body)
    return hmac.compare_digest(expected, signature)
