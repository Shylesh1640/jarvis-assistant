# Email Drafts

Draft emails locally; send only when an email provider is explicitly
configured.

## Data model

`email_drafts` table:

* `draft_id` (PK), `session_id` (indexed)
* `subject` (required, ≤256), `recipients` (JSON list, ≥1 valid address),
  `body` (nullable), `from_address` (nullable)
* `status`: `draft | sent`; `sent_at` stamped by `mark_sent`
* `created_at`, `updated_at`, `source_request_id`

Draft create/edit/list/delete are **fully local** and work with no email
provider at all.

## API

| Method | Path | Requires `?confirm=1` |
|---|---|---|
| POST | `/email-drafts` | no |
| GET | `/email-drafts` | no |
| GET | `/email-drafts/{draft_id}` | no |
| PATCH | `/email-drafts/{draft_id}` | no |
| DELETE | `/email-drafts/{draft_id}` | yes (physical delete) |
| POST | `/email-drafts/{draft_id}/send` | yes |

Validation: a subject is required; at least one recipient; every recipient
must be a valid email address (`jarvis.email.validation.is_valid_email`).

## Sending

Sending requires an `EmailProvider` implementation registered in
`EMAIL_PROVIDERS` and:

```
EMAIL_ENABLED=true
EMAIL_PROVIDER=smtp
EMAIL_CREDENTIALS_PATH=./credentials/email.json
EMAIL_DEFAULT_FROM=me@example.com
```

With no provider configured, `send` returns a structured
`503 email_not_configured` response (or tool message) and makes **no network
call**. The draft stays `draft` until a real send succeeds.

## Agent tools

* `list_email_drafts` — **low risk** (auto-run)
* `create_email_draft`, `update_email_draft` — **medium risk**, approval-gated
* `delete_email_draft`, `send_email_draft` — **high risk**, approval-gated

## Safety

* Providers read credentials from `EMAIL_CREDENTIALS_PATH`; never stored in
  the DB or logged.
* Full message bodies are never logged (subject + recipient count only).
* Drafts are session-scoped.