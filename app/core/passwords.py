"""Password hashing with scrypt from the standard library.

Stored format: ``scrypt$<n>$<r>$<p>$<salt b64>$<hash b64>``, so parameters can be raised
later without breaking existing hashes.
"""

import base64
import hashlib
import hmac
import secrets

_N, _R, _P = 2**14, 8, 1
_KEY_LENGTH = 32


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode(), salt=salt, n=n, r=r, p=p, dklen=_KEY_LENGTH, maxmem=64 * 1024 * 1024
    )


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _derive(password, salt, _N, _R, _P)
    encode = base64.b64encode
    return f"scrypt${_N}${_R}${_P}${encode(salt).decode()}${encode(digest).decode()}"


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time check. Hashes a dummy value when there is no stored hash, so a
    missing account takes as long to reject as a wrong password."""
    if not stored:
        _derive(password, b"0" * 16, _N, _R, _P)
        return False
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = _derive(password, base64.b64decode(salt), int(n), int(r), int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)
