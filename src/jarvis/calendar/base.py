"""Calendar provider abstraction (Phase 8).

The assistant never talks to a calendar backend directly — it talks to a
:class:`CalendarProvider` implementation resolved from ``settings`` through
the :data:`CALENDAR_PROVIDERS` registry.

No provider ships enabled by default: ``CALENDAR_ENABLED=false`` and
``CALENDAR_PROVIDER=""`` mean calendar routes/tools return a structured
"not configured" response and never touch the network.

Provider contract:
* keep credentials out of the DB and out of logs; read them from
  ``CALENDAR_CREDENTIALS_PATH``;
* never log event *contents* — ids and counts only;
* fail closed (raise) on missing credentials rather than guessing.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from jarvis.config.settings import settings

# Registry key -> provider class. Providers register themselves via
# ``register_provider`` (or by importing the provider module).
CALENDAR_PROVIDERS: dict[str, type["CalendarProvider"]] = {}


class CalendarEvent(BaseModel):
    """A single calendar event in transport format (never secrets).

    ``summary``/``start``/``end`` carry defaults so a *partial* event built
    from an update request can be passed to ``update_event`` (the provider
    merges only the fields it was given).
    """

    event_id: str = ""
    calendar_id: str = ""
    summary: str = Field("", max_length=500)
    description: str | None = None
    location: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    status: str = "confirmed"  # confirmed | tentative | cancelled


@runtime_checkable
class CalendarProvider(Protocol):
    """The operations a calendar backend must implement."""

    def health_check(self) -> dict:
        """Return {"ok": bool, "detail": str}; never includes credentials."""
        ...

    def list_calendars(self) -> list[dict]:
        """Return [{"calendar_id": str, "summary": str, "timezone": str}]."""
        ...

    def list_events(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        calendar_id: str | None = None,
    ) -> list[CalendarEvent]:
        """Events overlapping [start, end]; all events when bounds are None."""
        ...

    def create_event(self, calendar_id: str, event: CalendarEvent) -> str:
        """Create an event and return its provider event_id."""
        ...

    def update_event(self, event_id: str, event: CalendarEvent) -> str:
        """Replace the event's mutable fields; return its event_id."""
        ...

    def delete_event(self, event_id: str) -> bool:
        """Delete an event; True when it existed."""
        ...


def register_provider(name: str, cls: type[CalendarProvider]) -> None:
    """Register a provider class under a settings-friendly name."""
    CALENDAR_PROVIDERS[name] = cls


def get_provider() -> CalendarProvider | None:
    """Resolve the configured provider; None when disabled or unconfigured.

    Never touches the network and never raises for a missing config — the
    caller reports a structured "not configured" response instead.
    """
    if not settings.calendar_enabled:
        return None
    name = settings.calendar_provider
    if not name:
        return None
    cls = CALENDAR_PROVIDERS.get(name)
    if cls is None:
        return None
    try:
        return cls(settings)
    except Exception:  # noqa: BLE001
        return None


def not_configured_message() -> str:
    """Structured, user-actionable reason calendar features are unavailable."""
    if not settings.calendar_enabled:
        return (
            "Calendar is not configured: set CALENDAR_ENABLED=true to enable "
            "the calendar integration."
        )
    if not settings.calendar_provider:
        return (
            "Calendar is not configured: set CALENDAR_PROVIDER to a registered "
            "provider (e.g. 'google_calendar')."
        )
    return (
        "Calendar is not configured: provider "
        f"'{settings.calendar_provider}' is not registered."
    )


__all__ = [
    "CALENDAR_PROVIDERS",
    "CalendarEvent",
    "CalendarProvider",
    "get_provider",
    "not_configured_message",
    "register_provider",
]