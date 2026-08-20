"""Email draft management (Phase 8)."""
from jarvis.email.base import (
    EMAIL_PROVIDERS,
    EmailProvider,
    get_provider,
    not_configured_message,
    register_provider,
)
from jarvis.email.validation import is_valid_email

__all__ = [
    "EMAIL_PROVIDERS",
    "EmailProvider",
    "get_provider",
    "is_valid_email",
    "not_configured_message",
    "register_provider",
]