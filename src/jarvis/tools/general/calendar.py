"""LangChain tools for the calendar integration (Phase 8).

Every tool goes through the configured :class:`CalendarProvider`. When no
provider is enabled/configured they return a structured "not configured"
message and never touch the network. Reads are safe (low risk); writes are
approval-gated by the risk layer.
"""
from __future__ import annotations

from datetime import datetime

from langchain_core.tools import tool

from jarvis.calendar import CalendarEvent, get_provider, not_configured_message
from jarvis.config.settings import settings

_MAX_SUMMARY = 500


def _parse_dt(value: str | None) -> datetime | None:
    if value is None or value == "":
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _fmt_events(events: list[CalendarEvent]) -> str:
    if not events:
        return "No events found."
    lines = []
    for e in events:
        start = e.start.isoformat() if e.start else "?"
        lines.append(f"[{e.event_id}] {start} — {e.summary}")
    return "\n".join(lines)


def _provider_or_message():
    provider = get_provider()
    if provider is None:
        return None, not_configured_message()
    return provider, None


@tool
def list_calendars() -> str:
    """List the user's available calendars (ids + summaries). Read-only."""
    provider, message = _provider_or_message()
    if provider is None:
        return message
    calendars = provider.list_calendars()
    if not calendars:
        return "No calendars available."
    return "\n".join(
        f"[{c.get('calendar_id')}] {c.get('summary', '')}" for c in calendars
    )


@tool
def list_events(
    start: str | None = None,
    end: str | None = None,
    calendar_id: str | None = None,
    limit: int = 20,
) -> str:
    """List calendar events, optionally within an ISO-8601 [start, end] range.

    Read-only and safe. Returns a compact summary of each event.
    """
    provider, message = _provider_or_message()
    if provider is None:
        return message
    try:
        events = provider.list_events(
            start=_parse_dt(start),
            end=_parse_dt(end),
            calendar_id=calendar_id,
        )
    except ValueError as exc:
        return f"Error: invalid time bound ({exc}). Use ISO-8601."
    return _fmt_events(events[: max(1, min(limit, 200))])


@tool
def create_event(
    summary: str,
    start: str,
    end: str,
    calendar_id: str | None = None,
    description: str | None = None,
    location: str | None = None,
) -> str:
    """Create a calendar event.

    ``summary`` is required; ``start``/``end`` are ISO-8601 datetimes.
    ``calendar_id`` defaults to the configured default calendar.
    """
    provider, message = _provider_or_message()
    if provider is None:
        return message
    if not summary or not summary.strip():
        return "Error: a summary is required."
    if len(summary) > _MAX_SUMMARY:
        return f"Error: summary must be {_MAX_SUMMARY} characters or fewer."
    try:
        start_dt = _parse_dt(start)
        end_dt = _parse_dt(end)
    except ValueError as exc:
        return f"Error: invalid start/end ({exc}). Use ISO-8601."
    if start_dt is None or end_dt is None:
        return "Error: start and end are required."
    if end_dt <= start_dt:
        return "Error: event end must be after start."
    target = calendar_id or settings.calendar_default_calendar_id
    event = CalendarEvent(
        calendar_id=target or "",
        summary=summary.strip(),
        description=description,
        location=location,
        start=start_dt,
        end=end_dt,
    )
    event_id = provider.create_event(target or "", event)
    return f"Created calendar event {event_id}: {event.summary}"


@tool
def update_event(
    event_id: str,
    summary: str | None = None,
    start: str | None = None,
    end: str | None = None,
    description: str | None = None,
    location: str | None = None,
) -> str:
    """Update a calendar event's fields. Only provided fields are changed."""
    provider, message = _provider_or_message()
    if provider is None:
        return message
    if summary is not None and len(summary) > _MAX_SUMMARY:
        return f"Error: summary must be {_MAX_SUMMARY} characters or fewer."
    try:
        start_dt = _parse_dt(start)
        end_dt = _parse_dt(end)
    except ValueError as exc:
        return f"Error: invalid start/end ({exc}). Use ISO-8601."
    if start_dt is not None and end_dt is not None and end_dt <= start_dt:
        return "Error: event end must be after start."
    event = CalendarEvent(
        event_id=event_id,
        summary=summary or "",
        description=description,
        location=location,
        start=start_dt,
        end=end_dt,
    )
    provider.update_event(event_id, event)
    return f"Updated calendar event {event_id}."


@tool
def delete_event(event_id: str) -> str:
    """Delete a calendar event by its id. Requires approval."""
    provider, message = _provider_or_message()
    if provider is None:
        return message
    if not provider.delete_event(event_id):
        return f"Error: calendar event {event_id} not found."
    return f"Deleted calendar event {event_id}."


__all__ = ["list_calendars", "list_events", "create_event", "update_event", "delete_event"]