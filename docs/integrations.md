# Integrations & Personal Productivity (Phase 8)

Phase 8 adds local productivity features and an *opt-in* integration layer.
Everything is **off by default** and never touches a network or external
service until you explicitly configure a provider and (for writes) approve
the action.

## Feature matrix

| Feature | Local without provider? | Provider needed for | Master settings |
|---|---|---|---|
| Tasks & reminders | Yes (fully local) | — (reminders are in-app) | `TODO_REMINDER_*` |
| Calendar | No — needs a provider | list/create/update/delete events | `CALENDAR_ENABLED`, `CALENDAR_PROVIDER` |
| Email drafts | Yes (drafts only) | sending (`send_email_draft`) | `EMAIL_ENABLED`, `EMAIL_PROVIDER` |
| External connectors | No — needs config | execute | `CONNECTORS_ENABLED`, `CONNECTORS_CONFIG_PATH` |
| IDE integration | Needs a workspace root | — (runs locally, workspace-confined) | `IDE_INTEGRATION_ENABLED`, `IDE_WORKSPACE_ROOT` |
| Voice | No — needs a provider | transcribe / synthesize | `VOICE_INPUT_ENABLED`, `VOICE_OUTPUT_ENABLED`, `VOICE_*_PROVIDER` |

## Common safety rules

* Every integration is **disabled by default**. Unconfigured routes/tools
  return a structured `503 <feature>_not_configured` response and make no
  network call.
* Every **write** at the API layer requires `?confirm=1`; every write **tool**
  is approval-gated by the risk layer (pauses for human approval).
* Credentials are read by providers from credential files
  (`CALENDAR_CREDENTIALS_PATH`, `EMAIL_CREDENTIALS_PATH`,
  `VOICE_CREDENTIALS_PATH`). They are **never** stored in the database,
  returned in API responses, or written to logs. Audio, message bodies and
  event contents are never logged.
* All data stays session-scoped. A session can only ever see its own todos,
  drafts, etc.
* Tests use mock providers and never require external services, GPU, Docker,
  Ollama or the cloud.

## Provider abstraction

Calendar, email and voice follow the same pattern:

1. A `Protocol` interface in `src/jarvis/calendar/base.py`,
   `src/jarvis/email/base.py`, `src/jarvis/voice/base.py`.
2. A `*_PROVIDERS` registry mapping a settings-friendly name to an
   implementation class.
3. `get_provider()` resolves the configured provider (or `None`) — it never
   raises for a missing config and never touches the network.

To add a provider, implement the protocol, register it, then set the
corresponding settings. No provider ships enabled.

## Feature docs

* [Tasks & reminders](tasks-and-reminders.md)
* [Calendar integration](calendar-integration.md)
* [Email drafts](email-drafts.md)
* [External connectors](external-connectors.md)
* [IDE integration](ide-integration.md)
* [Voice interface](voice-interface.md)

## CLI

```
jarvis-todo list [--session SID] [--status S] [--priority P]
jarvis-todo add TITLE [--session SID] [--due ISO] [--priority P] [--yes]
jarvis-todo complete TODO_ID [--session SID] [--yes]
jarvis-todo delete TODO_ID [--session SID] [--yes]

jarvis-calendar list [--start ISO] [--end ISO] [--calendar-id ID]
jarvis-calendar add SUMMARY --start ISO --end ISO [--calendar-id ID] [--yes]

jarvis-email draft --subject SUBJ --recipients a@b.com,c@d.com [--session SID]
jarvis-email list [--session SID]
jarvis-email send DRAFT_ID [--session SID] [--yes]
```

Writes prompt for confirmation unless `--yes` is passed. No secrets or full
message bodies are ever printed.