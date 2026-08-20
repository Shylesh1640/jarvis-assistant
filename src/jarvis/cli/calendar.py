"""CLI: ``jarvis-calendar`` — calendar integration (Phase 8).

Usage::

    jarvis-calendar list [--start ISO] [--end ISO] [--calendar-id ID]
    jarvis-calendar add SUMMARY --start ISO --end ISO [--calendar-id ID]
                   [--description D] [--location L] [--yes]

Reads are safe; ``add`` is a write and prompts for confirmation unless
``--yes`` is passed. With no calendar provider configured the CLI prints the
structured "not configured" message and exits 1 — it never touches a network.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from jarvis.calendar import CalendarEvent, get_provider, not_configured_message
from jarvis.config.settings import settings


def _parse_dt(value: str | None) -> datetime | None:
    if value is None or value == "":
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _confirm(prompt: str, yes: bool) -> bool:
    if yes:
        return True
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="jarvis-calendar", description="Jarvis calendar CLI.")
    sub = parser.add_subparsers(dest="command", required=True)

    list_p = sub.add_parser("list", help="List events (optionally within a range).")
    list_p.add_argument("--start", default=None, help="ISO-8601 range start.")
    list_p.add_argument("--end", default=None, help="ISO-8601 range end.")
    list_p.add_argument("--calendar-id", default=None)

    add = sub.add_parser("add", help="Create a calendar event.")
    add.add_argument("summary")
    add.add_argument("--start", required=True, help="ISO-8601 start.")
    add.add_argument("--end", required=True, help="ISO-8601 end.")
    add.add_argument("--calendar-id", default=None)
    add.add_argument("--description", default=None)
    add.add_argument("--location", default=None)
    add.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    return parser.parse_args(argv)


def _cmd_list(args, provider) -> int:
    try:
        events = provider.list_events(
            start=_parse_dt(args.start), end=_parse_dt(args.end), calendar_id=args.calendar_id
        )
    except ValueError as exc:
        print(f"Error: invalid time bound ({exc}).")
        return 2
    if not events:
        print("No events found.")
        return 0
    for e in events:
        start = e.start.isoformat() if e.start else "?"
        print(f"[{e.event_id}] {start} — {e.summary}")
    return 0


def _cmd_add(args, provider) -> int:
    try:
        start = _parse_dt(args.start)
        end = _parse_dt(args.end)
    except ValueError as exc:
        print(f"Error: invalid start/end ({exc}).")
        return 2
    if start is None or end is None:
        print("Error: start and end are required.")
        return 2
    if end <= start:
        print("Error: event end must be after start.")
        return 2
    target = args.calendar_id or settings.calendar_default_calendar_id
    if not _confirm(
        f"Create calendar event '{args.summary}' ({start.isoformat()} → {end.isoformat()})?",
        args.yes,
    ):
        print("Cancelled.")
        return 1
    event = CalendarEvent(
        calendar_id=target or "",
        summary=args.summary,
        description=args.description,
        location=args.location,
        start=start,
        end=end,
    )
    event_id = provider.create_event(target or "", event)
    print(f"Created calendar event {event_id}: {event.summary}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    provider = get_provider()
    if provider is None:
        print(not_configured_message())
        return 1
    if args.command == "list":
        return _cmd_list(args, provider)
    if args.command == "add":
        return _cmd_add(args, provider)
    print(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())