"""Shared helpers for email drafts (routes, tools, CLI)."""
from __future__ import annotations

from jarvis.email.validation import is_valid_email


def validate_recipients(recipients: list[str]) -> str | None:
    """Return an error message when any recipient is invalid, else None."""
    if not recipients:
        return "At least one recipient is required."
    invalid = [r for r in recipients if not is_valid_email(r)]
    if invalid:
        return f"Invalid recipient address(es): {', '.join(invalid)}"
    return None


def draft_to_dict(row) -> dict:
    """Serialize an EmailDraftRow to the API response shape (no secrets)."""
    return {
        "draft_id": row.draft_id,
        "session_id": row.session_id,
        "subject": row.subject,
        "recipients": list(row.recipients or []),
        "body": row.body,
        "from_address": row.from_address,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
        "source_request_id": row.source_request_id,
    }


__all__ = ["draft_to_dict", "validate_recipients"]