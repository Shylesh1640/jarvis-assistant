"""Email provider abstraction (Phase 8).

Like the calendar integration, the assistant never sends mail through a
hardcoded backend — it goes through an :class:`EmailProvider` resolved from
``settings`` via the :data:`EMAIL_PROVIDERS` registry.

No provider ships enabled: ``EMAIL_ENABLED=false`` and ``EMAIL_PROVIDER=""``
mean email send returns a structured "not configured" response and makes no
network call. Draft creation/list/edit/delete are fully local (the
``email_drafts`` table) and work with no provider at all.

Provider contract:
* read credentials from ``EMAIL_CREDENTIALS_PATH``; never store or log them;
* never log message bodies (subject + recipient count only);
* fail closed on missing credentials.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from jarvis.config.settings import settings

EMAIL_PROVIDERS: dict[str, type["EmailProvider"]] = {}


@runtime_checkable
class EmailProvider(Protocol):
    """The operations an email backend must implement."""

    def health_check(self) -> dict:
        """Return {"ok": bool, "detail": str}; never includes credentials."""
        ...

    def send(
        self,
        *,
        subject: str,
        recipients: list[str],
        body: str | None = None,
        from_address: str | None = None,
    ) -> str:
        """Send an email; return the provider's message id."""
        ...


def register_provider(name: str, cls: type[EmailProvider]) -> None:
    """Register a provider class under a settings-friendly name."""
    EMAIL_PROVIDERS[name] = cls


def get_provider() -> EmailProvider | None:
    """Resolve the configured provider; None when disabled or unconfigured."""
    if not settings.email_enabled:
        return None
    name = settings.email_provider
    if not name:
        return None
    cls = EMAIL_PROVIDERS.get(name)
    if cls is None:
        return None
    try:
        return cls(settings)
    except Exception:  # noqa: BLE001
        return None


def not_configured_message() -> str:
    """Structured, user-actionable reason email sending is unavailable."""
    if not settings.email_enabled:
        return (
            "Email is not configured: set EMAIL_ENABLED=true to enable email "
            "sending. Drafts can still be created and edited locally."
        )
    if not settings.email_provider:
        return (
            "Email is not configured: set EMAIL_PROVIDER to a registered "
            "provider (e.g. 'smtp')."
        )
    return f"Email is not configured: provider '{settings.email_provider}' is not registered."


__all__ = [
    "EMAIL_PROVIDERS",
    "EmailProvider",
    "get_provider",
    "not_configured_message",
    "register_provider",
]