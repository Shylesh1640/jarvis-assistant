"""LangChain tools for email drafts (Phase 8).

Draft create/list/update/delete are fully local (the ``email_drafts`` table)
and work with no provider. ``send_email_draft`` is approval-gated (high
risk) and, without a configured ``EMAIL_PROVIDER``, returns a structured
"not configured" response and never touches the network.
"""
from __future__ import annotations

import uuid

from langchain_core.tools import tool

from jarvis.email import get_provider, not_configured_message
from jarvis.email.drafts import validate_recipients
from jarvis.persistence import repos

_MAX_SUBJECT = 256


@tool
def list_email_drafts(
    session_id: str = "default",
    status: str | None = None,
    limit: int = 50,
) -> str:
    """List email drafts for a session (subject + recipient list). Read-only."""
    if status and status not in ("draft", "sent"):
        return "invalid status; must be one of ['draft', 'sent']."
    rows = repos.email_drafts.list_for_session(
        session_id, status=status, limit=max(1, min(limit, 200))
    )
    if not rows:
        return "No email drafts found."
    return "\n".join(
        f"[{r.draft_id}] ({r.status}) {r.subject} → {', '.join(r.recipients or [])}"
        for r in rows
    )


@tool
def create_email_draft(
    subject: str,
    recipients: list[str],
    session_id: str = "default",
    body: str | None = None,
    from_address: str | None = None,
) -> str:
    """Create a local email draft (nothing is sent).

    ``subject`` is required; ``recipients`` must contain at least one valid
    email address. The draft is stored locally and can be edited later.
    """
    if not subject or not subject.strip():
        return "Error: a subject is required."
    if len(subject) > _MAX_SUBJECT:
        return f"Error: subject must be {_MAX_SUBJECT} characters or fewer."
    error = validate_recipients(recipients or [])
    if error:
        return f"Error: {error}"
    row = repos.email_drafts.create(
        uuid.uuid4().hex,
        session_id,
        subject=subject.strip(),
        recipients=list(recipients),
        body=body,
        from_address=from_address,
    )
    return f"Created email draft {row.draft_id}: {row.subject}"


@tool
def update_email_draft(
    draft_id: str,
    session_id: str = "default",
    subject: str | None = None,
    recipients: list[str] | None = None,
    body: str | None = None,
) -> str:
    """Edit a local email draft. Only provided fields are changed."""
    if subject is not None and not subject.strip():
        return "Error: subject cannot be empty."
    if subject is not None and len(subject) > _MAX_SUBJECT:
        return f"Error: subject must be {_MAX_SUBJECT} characters or fewer."
    if recipients is not None:
        error = validate_recipients(recipients)
        if error:
            return f"Error: {error}"
    row = repos.email_drafts.update(
        session_id,
        draft_id,
        subject=subject.strip() if subject else None,
        recipients=recipients,
        body=body,
    )
    if row is None:
        return f"Error: email draft {draft_id} not found in session {session_id}."
    return f"Updated email draft {draft_id}."


@tool
def delete_email_draft(draft_id: str, session_id: str = "default") -> str:
    """Delete a local email draft. Requires approval."""
    if not repos.email_drafts.delete(session_id, draft_id):
        return f"Error: email draft {draft_id} not found in session {session_id}."
    return f"Deleted email draft {draft_id}."


@tool
def send_email_draft(draft_id: str, session_id: str = "default") -> str:
    """Send a previously-created email draft. Requires approval.

    Fails with a structured message (never a network call) when no email
    provider is configured.
    """
    provider = get_provider()
    if provider is None:
        return not_configured_message()
    row = repos.email_drafts.get(session_id, draft_id)
    if row is None:
        return f"Error: email draft {draft_id} not found in session {session_id}."
    message_id = provider.send(
        subject=row.subject,
        recipients=list(row.recipients or []),
        body=row.body,
        from_address=row.from_address,
    )
    repos.email_drafts.mark_sent(session_id, draft_id)
    return f"Sent email draft {draft_id} (message {message_id})."


__all__ = [
    "list_email_drafts",
    "create_email_draft",
    "update_email_draft",
    "delete_email_draft",
    "send_email_draft",
]