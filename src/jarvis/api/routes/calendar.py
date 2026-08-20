"""Routes for the calendar integration (Phase 8).

* ``GET    /calendar/calendars``          — list calendars
* ``GET    /calendar/events``             — list events (optional start/end)
* ``POST   /calendar/events?confirm=1``   — create an event (write, needs confirm)
* ``PATCH  /calendar/events/{id}?confirm=1`` — update an event (write, needs confirm)
* ``DELETE /calendar/events/{id}?confirm=1`` — delete an event (write, needs confirm)

Every write requires ``?confirm=1`` so a misconfigured client can never
mutate a calendar by accident. When no provider is configured the routes
return a structured ``503 calendar_not_configured`` response and never
touch the network. No credentials or event contents are ever logged.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter

from jarvis.api.errors import APIError
from jarvis.api.schemas.calendar import CalendarEventCreate, CalendarEventUpdate
from jarvis.calendar import CalendarEvent, get_provider, not_configured_message
from jarvis.config.settings import settings

router = APIRouter(prefix="/calendar", tags=["calendar"])


def _provider():
    provider = get_provider()
    if provider is None:
        raise APIError(503, "calendar_not_configured", not_configured_message())
    return provider


def _check_event_times(start: datetime | None, end: datetime | None) -> None:
    if start is not None and end is not None and end <= start:
        raise APIError(
            422,
            "invalid_event_times",
            "Event end must be after start.",
        )


@router.get("/calendars")
def calendar_list_calendars() -> dict:
    return {"items": _provider().list_calendars()}


@router.get("/events")
def calendar_list_events(
    start: datetime | None = None,
    end: datetime | None = None,
    calendar_id: str | None = None,
    limit: int = 100,
) -> dict:
    events = _provider().list_events(start=start, end=end, calendar_id=calendar_id)
    capped = min(len(events), max(1, min(limit, 500)))
    return {"items": [e.model_dump() for e in events[:capped]], "count": capped}


@router.post("/events")
def calendar_create_event(payload: CalendarEventCreate, confirm: bool = False) -> dict:
    if not confirm:
        raise APIError(
            400,
            "confirmation_required",
            "Pass ?confirm=1 to create this calendar event.",
        )
    _check_event_times(payload.start, payload.end)
    calendar_id = payload.calendar_id or settings.calendar_default_calendar_id
    event = CalendarEvent(
        calendar_id=calendar_id or "",
        summary=payload.summary,
        description=payload.description,
        location=payload.location,
        start=payload.start,
        end=payload.end,
    )
    event_id = _provider().create_event(calendar_id or "", event)
    return {"event_id": event_id, "created": True}


@router.patch("/events/{event_id}")
def calendar_update_event(
    event_id: str, payload: CalendarEventUpdate, confirm: bool = False
) -> dict:
    if not confirm:
        raise APIError(
            400,
            "confirmation_required",
            "Pass ?confirm=1 to update this calendar event.",
        )
    _check_event_times(payload.start, payload.end)
    event = CalendarEvent(
        event_id=event_id,
        calendar_id=payload.calendar_id or settings.calendar_default_calendar_id or "",
        summary=payload.summary or "",
        description=payload.description,
        location=payload.location,
        start=payload.start,
        end=payload.end,
    )
    _provider().update_event(event_id, event)
    return {"event_id": event_id, "updated": True}


@router.delete("/events/{event_id}")
def calendar_delete_event(event_id: str, confirm: bool = False) -> dict:
    if not confirm:
        raise APIError(
            400,
            "confirmation_required",
            "Pass ?confirm=1 to delete this calendar event.",
        )
    if not _provider().delete_event(event_id):
        raise APIError(404, "event_not_found", f"Calendar event '{event_id}' not found.")
    return {"event_id": event_id, "deleted": True}