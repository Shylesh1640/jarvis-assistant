"""Password hashing utilities (Phase 11 :: user management).

Secure password hashing using Argon2 (preferred), bcrypt, or PBKDF2 fallback.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_PBKDF2_ITERATIONS = 100_000
_HASH_SCHEMES = ("argon2", "bcrypt", "pbkdf2")


def _argon2_hasher():
    from argon2 import PasswordHasher

    return PasswordHasher()


def hash_password(password: str, scheme: str | None = None) -> str:
    """Return a scheme-prefixed, self-describing hash of *password*."""
    from jarvis.config.settings import settings

    chosen = (scheme or settings.session_token_hash_scheme or "argon2").lower()
    if chosen == "bcrypt":
        import bcrypt

        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
    if chosen == "pbkdf2":
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
        )
        return (
            f"pbkdf2${_PBKDF2_ITERATIONS}$"
            f"{base64.b64encode(salt).decode('ascii')}$"
            f"{base64.b64encode(digest).decode('ascii')}"
        )
    # argon2 (default)
    return _argon2_hasher().hash(password)


def verify_password(password: str, stored: str | None) -> bool:
    """True when *password* matches the stored *stored* value.

    Dispatches on the stored format; a stored value that does not look like
    any supported hash is compared as legacy plaintext.
    """
    if not stored or password is None:
        return False
    if stored.startswith("$argon2"):
        try:
            return _argon2_hasher().verify(stored, password)
        except Exception:  # noqa: BLE001 — VerificationError/MismatchError
            return False
    if stored.startswith("$2"):
        try:
            import bcrypt

            return bcrypt.checkpw(password.encode("utf-8"), stored.encode("ascii"))
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
            digest = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), salt, iterations
            )
            return hmac.compare_digest(digest, expected)
        except Exception:  # noqa: BLE001
            return False
    # Legacy plaintext password (should not happen in production).
    return hmac.compare_digest(password, stored)


def new_password_reset_token() -> str:
    """Generate a cryptographically random password reset token."""
    return secrets.token_urlsafe(32)


HASH_SCHEMES: tuple[str, ...] = _HASH_SCHEMES

__all__ = [
    "HASH_SCHEMES",
    "hash_password",
    "verify_password",
    "new_password_reset_token",
]