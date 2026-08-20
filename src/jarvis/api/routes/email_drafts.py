"""Routes for email drafts (Phase 8).

* ``POST   /email-drafts``                      — create a local draft
* ``GET    /email-drafts``                      — list drafts for a session
* ``GET    /email-drafts/{draft_id}``           — one draft
* ``PATCH  /email-drafts/{draft_id}``           — edit a draft
* ``DELETE /email-drafts/{draft_id}?confirm=1`` — delete a draft (needs confirm)
* ``POST   /email-drafts/{draft_id}/send?confirm=1`` — send (needs confirm AND a
  configured EMAIL_PROVIDER; otherwise a structured 503, never a network call)

Draft create/edit/list/delete are fully local and work with no email
provider. Sending requires ``EMAIL_ENABLED=true`` + ``EMAIL_PROVIDER``.
No credentials are stored or logged; full bodies are never logged.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from jarvis.api.errors import APIError
from jarvis.api.schemas.email_drafts import EmailDraftCreate, EmailDraftUpdate
from jarvis.config.settings import settings
from jarvis.email import get_provider, not_configured_message
from jarvis.email.drafts import draft_to_dict, validate_recipients
from jarvis.persistence import create_all, repos
from jarvis.security.session_auth import ensure_session_context

logger = logging.getLogger("jarvis.api.email_drafts")

router = APIRouter(prefix="/email-drafts", tags=["email-drafts"])


class EmailDraftSendPayload(BaseModel):
    session_id: str = "default"
    session_token: str | None = None


def _ensure_db() -> None:
    try:
        create_all()
    except Exception as exc:  # noqa: BLE001
        logger.debug("create_all failed in email-drafts route: %s", exc)


def _sid(session_id: str | None, session_token: str | None) -> str:
    sid = session_id or "default"
    ensure_session_context(sid, session_token)
    return sid


def _validate_recipients_or_422(recipients: list[str]) -> None:
    error = validate_recipients(recipients)
    if error:
        raise APIError(422, "invalid_recipients", error)


@router.post("")
def draft_create(payload: EmailDraftCreate) -> dict:
    ensure_session_context(payload.session_id, payload.session_token)
    _validate_recipients_or_422(payload.recipients)
    _ensure_db()
    row = repos.email_drafts.create(
        uuid.uuid4().hex,
        payload.session_id,
        subject=payload.subject,
        recipients=payload.recipients,
        body=payload.body,
        from_address=payload.from_address,
        source_request_id=payload.source_request_id,
    )
    return draft_to_dict(row)


@router.get("")
def draft_list(
    session_id: str | None = None,
    session_token: str | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    sid = _sid(session_id, session_token)
    _ensure_db()
    if status and status not in ("draft", "sent"):
        raise APIError(422, "invalid_draft_status", "status must be one of ['draft', 'sent'].")
    rows = repos.email_drafts.list_for_session(
        sid,
        status=status,
        limit=max(0, min(limit, 500)),
        offset=max(0, offset),
    )
    return {"items": [draft_to_dict(r) for r in rows], "count": len(rows)}


@router.get("/{draft_id}")
def draft_get(
    draft_id: str,
    session_id: str | None = None,
    session_token: str | None = None,
) -> dict:
    sid = _sid(session_id, session_token)
    _ensure_db()
    row = repos.email_drafts.get(sid, draft_id)
    if row is None:
        raise APIError(404, "draft_not_found", "Email draft not found.")
    return draft_to_dict(row)


@router.patch("/{draft_id}")
def draft_update(draft_id: str, payload: EmailDraftUpdate) -> dict:
    ensure_session_context(payload.session_id, payload.session_token)
    if payload.recipients is not None:
        _validate_recipients_or_422(payload.recipients)
    _ensure_db()
    row = repos.email_drafts.update(
        payload.session_id,
        draft_id,
        subject=payload.subject,
        recipients=payload.recipients,
        body=payload.body,
        from_address=payload.from_address,
    )
    if row is None:
        raise APIError(404, "draft_not_found", "Email draft not found.")
    return draft_to_dict(row)


@router.delete("/{draft_id}")
def draft_delete(
    draft_id: str,
    confirm: bool = False,
    session_id: str | None = None,
    session_token: str | None = None,
) -> dict:
    sid = _sid(session_id, session_token)
    if not confirm:
        raise APIError(
            400,
            "confirmation_required",
            "Pass ?confirm=1 to delete this email draft.",
        )
    _ensure_db()
    if not repos.email_drafts.delete(sid, draft_id):
        raise APIError(404, "draft_not_found", "Email draft not found.")
    return {"deleted": draft_id}


@router.post("/{draft_id}/send")
def draft_send(
    draft_id: str,
    payload: EmailDraftSendPayload | None = None,
    confirm: bool = False,
) -> dict:
    payload = payload or EmailDraftSendPayload()
    ensure_session_context(payload.session_id, payload.session_token)
    if not confirm:
        raise APIError(
            400,
            "confirmation_required",
            "Pass ?confirm=1 to send this email draft.",
        )
    provider = get_provider()
    if provider is None:
        raise APIError(503, "email_not_configured", not_configured_message())
    _ensure_db()
    row = repos.email_drafts.get(payload.session_id, draft_id)
    if row is None:
        raise APIError(404, "draft_not_found", "Email draft not found.")
    from_address = row.from_address or settings.email_default_from
    logger.info(
        "Sending email draft %s (recipients=%d, subject=%s)",
        draft_id,
        len(row.recipients or []),
        row.subject,
    )
    message_id = provider.send(
        subject=row.subject,
        recipients=list(row.recipients or []),
        body=row.body,
        from_address=from_address,
    )
    sent = repos.email_drafts.mark_sent(payload.session_id, draft_id)
    return {
        "draft_id": draft_id,
        "status": sent.status if sent else "sent",
        "message_id": message_id,
        "sent": True,
    }