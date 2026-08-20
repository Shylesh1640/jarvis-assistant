"""Session-token hashing (Phase 6).

Tokens are stored *hashed* at rest — the plaintext appears only in the
response of ``GET /sessions/{id}/token`` (and its rotation/issuance calls)
and is never written to the database.

Supported schemes (``SESSION_TOKEN_HASH_SCHEME``):

* ``argon2`` — recommended; Argon2id via ``argon2-cffi``.
* ``bcrypt`` — via ``bcrypt``.
* ``pbkdf2`` — pure-stdlib fallback (PBKDF2-HMAC-SHA256, 100k iterations).

Stored values are self-describing so ``verify_token`` can dispatch without
a separate scheme column:

* Argon2/Bcrypt hashes already begin with ``$argon2id$`` / ``$2b$``.
* PBKDF2 is stored as ``pbkdf2$<iterations>$<salt_b64>$<hash_b64>``.
* Anything else is treated as a legacy plaintext token (compared directly).

Failures are fail-safe: a malformed stored value or missing scheme
dependency makes ``verify_token`` return False (never True).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_PBKDF2_ITERATIONS = 100_000
_HASH_SCHEMES = ("argon2", "bcrypt", "pbkdf2")


def new_session_token() -> str:
    """Generate a cryptographically random session token (256-bit URL-safe)."""
    return secrets.token_urlsafe(32)


def _argon2_hasher():
    from argon2 import PasswordHasher

    return PasswordHasher()


def hash_token(token: str, scheme: str | None = None) -> str:
    """Return a scheme-prefixed, self-describing hash of *token*."""
    from jarvis.config.settings import settings

    chosen = (scheme or settings.session_token_hash_scheme or "argon2").lower()
    if chosen == "bcrypt":
        import bcrypt

        return bcrypt.hashpw(token.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
    if chosen == "pbkdf2":
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256", token.encode("utf-8"), salt, _PBKDF2_ITERATIONS
        )
        return (
            f"pbkdf2${_PBKDF2_ITERATIONS}$"
            f"{base64.b64encode(salt).decode('ascii')}$"
            f"{base64.b64encode(digest).decode('ascii')}"
        )
    # argon2 (default)
    return _argon2_hasher().hash(token)


def verify_token(token: str, stored: str | None) -> bool:
    """True when *token* matches the stored *stored* value.

    Dispatches on the stored format; a stored value that does not look like
    any supported hash is compared as legacy plaintext (used during the
    one-time lazy migration of pre-hash tokens).
    """
    if not stored or token is None:
        return False
    if stored.startswith("$argon2"):
        try:
            return _argon2_hasher().verify(stored, token)
        except Exception:  # noqa: BLE001 — VerificationError/MismatchError
            return False
    if stored.startswith("$2"):
        try:
            import bcrypt

            return bcrypt.checkpw(token.encode("utf-8"), stored.encode("ascii"))
        except Exception:  # noqa: BLE001
            return False
    if stored.startswith("pbkdf2$"):
        try:
            parts = stored.split("$")
            if len(parts) != 4:
                return False
            iterations = int(parts[1])
            salt = base64.b64decode(parts[2])
            expected = base64.b64decode(parts[3])
            digest = hashlib.pbkdf2_hmac("sha256", token.encode("utf-8"), salt, iterations)
            return hmac.compare_digest(digest, expected)
        except Exception:  # noqa: BLE001
            return False
    # Legacy plaintext token (pre-Phase 6 rows).
    return hmac.compare_digest(token, stored)


def looks_hashed(stored: str | None) -> bool:
    """True when *stored* is a recognised hash format (not legacy plaintext)."""
    if not stored:
        return False
    return stored.startswith("$argon2") or stored.startswith("$2") or stored.startswith("pbkdf2$")


HASH_SCHEMES: tuple[str, ...] = _HASH_SCHEMES

__all__ = [
    "HASH_SCHEMES",
    "hash_token",
    "looks_hashed",
    "new_session_token",
    "verify_token",
]