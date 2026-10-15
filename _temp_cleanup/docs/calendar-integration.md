# Calendar Integration

The assistant talks to calendars through a **provider abstraction** — never a
hardcoded backend.

## How it works

* `CalendarProvider` is a `Protocol` in `src/jarvis/calendar/base.py` with
  `health_check`, `list_calendars`, `list_events`, `create_event`,
  `update_event`, `delete_event`.
* `CALENDAR_PROVIDERS` maps a settings-friendly name to an implementation
  class. No provider ships enabled.
* `get_provider()` resolves the configured provider, or returns `None` when
  disabled/unconfigured — it never touches the network and never raises for
  a missing config.

To enable, you must **provide an implementation** (e.g. `google_calendar`)
and set:

```
CALENDAR_ENABLED=true
CALENDAR_PROVIDER=google_calendar
CALENDAR_CREDENTIALS_PATH=./credentials/calendar.json
CALENDAR_DEFAULT_CALENDAR_ID=primary
```

Until then, calendar routes/tools return a structured
`503 calendar_not_configured` response.

## API

| Method | Path | Requires `?confirm=1` |
|---|---|---|
| GET | `/calendar/calendars` | no |
| GET | `/calendar/events` | no |
| POST | `/calendar/events` | yes |
| PATCH | `/calendar/events/{id}` | yes |
| DELETE | `/calendar/events/{id}` | yes |

## Agent tools

* `list_calendars`, `list_events` — **low risk** (auto-run)
* `create_event`, `update_event` — **medium risk**, approval-gated
* `delete_event` — **high risk**, approval-gated

## Safety

* No provider enabled by default; nothing runs until configured.
* All writes require human confirmation (`confirm=1` / approval).
* Credentials are read by the provider from `CALENDAR_CREDENTIALS_PATH`;
  they are never stored in the DB, returned in responses, or logged.
* Event contents are never logged — only ids and counts.