"""Calendar provider abstraction (Phase 8)."""
from jarvis.calendar.base import (
    CALENDAR_PROVIDERS,
    CalendarEvent,
    CalendarProvider,
    get_provider,
    not_configured_message,
    register_provider,
)

__all__ = [
    "CALENDAR_PROVIDERS",
    "CalendarEvent",
    "CalendarProvider",
    "get_provider",
    "not_configured_message",
    "register_provider",
]