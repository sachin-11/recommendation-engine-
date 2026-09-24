"""API key generation and hashing.

Keys look like ``reco_<43 url-safe chars>``. Only a keyed SHA-256 digest
(HMAC-SHA256 with SECRET_KEY) is persisted, so a leaked database alone cannot be
used to brute-force or verify keys. Rotating SECRET_KEY invalidates every key.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from app.core.config import settings

API_KEY_PREFIX = "reco_"
KEY_PREFIX_LENGTH = 8


@dataclass(frozen=True, slots=True)
class GeneratedApiKey:
    plain_key: str
    key_hash: str
    key_prefix: str


def hash_api_key(plain_key: str) -> str:
    secret = settings.SECRET_KEY.get_secret_value().encode()
    return hmac.new(secret, plain_key.encode(), hashlib.sha256).hexdigest()


def verify_api_key(plain_key: str, key_hash: str) -> bool:
    return hmac.compare_digest(hash_api_key(plain_key), key_hash)


def generate_api_key() -> GeneratedApiKey:
    token = secrets.token_urlsafe(32)
    plain_key = f"{API_KEY_PREFIX}{token}"
    return GeneratedApiKey(
        plain_key=plain_key,
        key_hash=hash_api_key(plain_key),
        key_prefix=token[:KEY_PREFIX_LENGTH],
    )
