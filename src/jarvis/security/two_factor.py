"""Two-Factor Authentication utilities (Phase 12).

TOTP-based 2FA using pyotp. Secrets are encrypted at rest.
"""
from __future__ import annotations

import base64
import secrets

import pyotp
from cryptography.fernet import Fernet

from jarvis.config.settings import settings


# ---------------------------------------------------------------------------
# Encryption for 2FA secrets
# ---------------------------------------------------------------------------

def _get_encryption_key() -> bytes:
    """Get or derive the encryption key for 2FA secrets."""
    # Use a separate key for 2FA encryption, derived from the main secret
    # In production, this should come from a proper key management system
    base_key = settings.session_token_hash_scheme.encode() + b"jarvis-2fa"
    # Derive a 32-byte key using PBKDF2
    import hashlib
    key = hashlib.pbkdf2_hmac("sha256", base_key, b"jarvis-2fa-salt", 100000, dklen=32)
    return base64.urlsafe_b64encode(key)


def _get_cipher() -> Fernet:
    """Get a Fernet cipher for encrypting/decrypting 2FA secrets."""
    return Fernet(_get_encryption_key())


def encrypt_2fa_secret(secret: str) -> str:
    """Encrypt a 2FA secret for storage."""
    cipher = _get_cipher()
    return cipher.encrypt(secret.encode()).decode()


def decrypt_2fa_secret(encrypted: str) -> str:
    """Decrypt a 2FA secret from storage."""
    cipher = _get_cipher()
    return cipher.decrypt(encrypted.encode()).decode()


# ---------------------------------------------------------------------------
# TOTP operations
# ---------------------------------------------------------------------------

def generate_totp_secret() -> str:
    """Generate a new TOTP secret (base32 encoded)."""
    return pyotp.random_base32()


def get_totp_uri(secret: str, email: str, issuer: str = "Jarvis") -> str:
    """Get the provisioning URI for QR code generation."""
    return pyotp.totp.TOTP(secret).provisioning_uri(name=email, issuer_name=issuer)


def verify_totp(secret: str, code: str, valid_window: int = 1) -> bool:
    """Verify a TOTP code against the secret.
    
    Args:
        secret: The TOTP secret (base32)
        code: The 6-digit code to verify
        valid_window: Number of time steps to allow (default 1 = current + previous)
    """
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=valid_window)


# ---------------------------------------------------------------------------
# Recovery codes
# ---------------------------------------------------------------------------

def generate_recovery_codes(count: int = 10, length: int = 8) -> list[str]:
    """Generate a list of one-time recovery codes."""
    return [secrets.token_hex(length // 2).upper() for _ in range(count)]


def hash_recovery_code(code: str) -> str:
    """Hash a recovery code for storage (using the same scheme as passwords)."""
    from jarvis.security.password_hasher import hash_password
    return hash_password(code)


def verify_recovery_code(code: str, stored_hash: str) -> bool:
    """Verify a recovery code against its hash."""
    from jarvis.security.password_hasher import verify_password
    return verify_password(code, stored_hash)


# ---------------------------------------------------------------------------
# Remember device token
# ---------------------------------------------------------------------------

def generate_remember_token() -> str:
    """Generate a remember-device token."""
    return secrets.token_urlsafe(32)


def hash_remember_token(token: str) -> str:
    """Hash a remember-device token for storage."""
    from jarvis.security.token_hasher import hash_token
    from jarvis.config.settings import settings
    return hash_token(token, settings.session_token_hash_scheme)


def verify_remember_token(token: str, stored_hash: str) -> bool:
    """Verify a remember-device token against its hash."""
    from jarvis.security.token_hasher import verify_token
    return verify_token(token, stored_hash)


__all__ = [
    "encrypt_2fa_secret",
    "decrypt_2fa_secret",
    "generate_totp_secret",
    "get_totp_uri",
    "verify_totp",
    "generate_recovery_codes",
    "hash_recovery_code",
    "verify_recovery_code",
    "generate_remember_token",
    "hash_remember_token",
    "verify_remember_token",
]