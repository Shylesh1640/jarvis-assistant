# Tasks & Reminders

Fully-local to-do items per session, with a background reminder worker.
No external service is ever involved.

## Data model

`todos` table:

* `todo_id` (PK), `session_id` (indexed)
* `title` (required, ≤256), `description` (nullable)
* `status`: `open → in_progress → completed | cancelled` (forward-only;
  terminal states never move again)
* `priority`: `low | medium | high`
* `due_at` (nullable, indexed), `created_at`, `updated_at`
* `completed_at` — stamped only when status becomes `completed`
* `source_request_id` (nullable)
* `deleted_at` — soft-delete marker (history is preserved; the worker never
  re-fires for deleted todos)
* `last_reminded_at` — dedupes the reminder worker

Deletion is a **soft delete**: `DELETE /todos/{id}` sets `deleted_at` and
marks the todo `cancelled`. It is reversible and needs no `confirm=1`.

## API

| Method | Path | Notes |
|---|---|---|
| POST | `/todos` | create (session-scoped) |
| GET | `/todos` | list; filters `status`, `priority`, `due_before`, `due_after`, `limit`, `offset` |
| GET | `/todos/{todo_id}` | one todo |
| PATCH | `/todos/{todo_id}` | update fields; status transitions validated |
| DELETE | `/todos/{todo_id}` | soft delete |
| POST | `/todos/{todo_id}/complete` | mark completed |

Every call is scoped by `session_id` and validated through
`ensure_session_context`, so one session can never read or mutate another's
todos.

## Agent tools

The LLM can request:

* `list_todos` — **low risk**, runs automatically
* `create_todo`, `complete_todo`, `update_todo` — **medium risk**,
  approval-gated
* `delete_todo` — **high risk**, approval-gated

## Reminder worker

`jarvis.tasks.reminders.scan_once` (a daemon thread, mirroring the
maintenance sweeper) finds active todos whose `due_at` is within
`TODO_REMINDER_LOOKAHEAD_MINUTES` (default 30) and have not been reminded
yet. It writes an assistant message into the owning session (so it appears
in the next chat) and stamps `last_reminded_at` so it fires once.

Reminders are **local-only** — they never send external emails, SMS or
push notifications.

## Settings

```
TODO_REMINDER_SCAN_INTERVAL_SECONDS=300   # 0 disables the worker thread (a
                                         # single scan still runs at startup)
TODO_REMINDER_LOOKAHEAD_MINUTES=30
```