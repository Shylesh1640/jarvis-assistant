"""Routes for user feedback on assistant replies.

Phase 6 :: Feedback quality.

* ``POST /feedback`` — submit a rating (score: good / bad / unclear) for a
  specific reply, with an optional comment.
* ``GET /feedback`` — list recent feedback (for review / the Streamlit panel).
* ``DELETE /feedback/{id}?confirm=1`` — delete one entry (destructive, so a
  confirmation flag is required).
* ``DELETE /feedback?confirm=1`` — clear all feedback.

Feedback is always best-effort: if the DB is unavailable the POST still
succeeds with ``stored: false`` so the chat UI never breaks.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from jarvis.api.errors import APIError
from jarvis.persistence import create_all, repos
from jarvis.security.session_auth import ensure_session_context

logger = logging.getLogger("jarvis.api.feedback")

router = APIRouter(prefix="/feedback", tags=["feedback"])

_VALID_SCORES = {"good", "bad", "unclear"}


class FeedbackRequest(BaseModel):
    session_id: str = "default"
    session_token: str | None = None
    question: str = ""
    answer: str = ""
    score: str = Field(...)
    comment: str | None = None
    path_used: str | None = None
    model_used: str | None = None


def _ensure_db() -> None:
    try:
        create_all()
    except Exception as exc:  # noqa: BLE001
        logger.debug("create_all failed in feedback route: %s", exc)


@router.post("")
def feedback_submit(payload: FeedbackRequest) -> dict:
    ensure_session_context(payload.session_id, payload.session_token)
    score = payload.score
    if score not in _VALID_SCORES:
        raise APIError(
            422,
            "invalid_score",
            f"score must be one of {sorted(_VALID_SCORES)}.",
        )
    if not payload.answer.strip():
        raise APIError(400, "missing_answer", "The rated reply must not be empty.")

    _ensure_db()
    try:
        feedback_id = repos.feedback.add(
            payload.session_id,
            question=payload.question,
            answer=payload.answer,
            score=score,
            comment=payload.comment,
            path_used=payload.path_used,
            model_used=payload.model_used,
        )
        return {"stored": True, "id": feedback_id}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Storing feedback failed: %s", exc)
        return {"stored": False, "id": None}


@router.get("")
def feedback_list(session_id: str | None = None, session_token: str | None = None) -> dict:
    ensure_session_context(session_id or "default", session_token)
    _ensure_db()
    try:
        if session_id:
            rows = repos.feedback.list_for_session(session_id)
        else:
            rows = repos.feedback.list()
        return {
            "items": [_row_to_dict(r) for r in rows],
            "count": len(rows),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Listing feedback failed: %s", exc)
        return {"items": [], "count": 0}


@router.delete("/{feedback_id}")
def feedback_delete(
    feedback_id: int,
    session_id: str | None = None,
    session_token: str | None = None,
    confirm: bool = False,
) -> dict:
    ensure_session_context(session_id or "default", session_token)
    if not confirm:
        raise APIError(
            400,
            "confirmation_required",
            "Pass ?confirm=1 to delete this feedback entry.",
        )
    _ensure_db()
    try:
        removed = repos.feedback.delete(feedback_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Deleting feedback failed: %s", exc)
        removed = False
    if not removed:
        raise APIError(404, "feedback_not_found", "Feedback entry not found.")
    return {"deleted": feedback_id}


@router.delete("")
def feedback_clear(
    session_id: str | None = None,
    session_token: str | None = None,
    confirm: bool = False,
) -> dict:
    ensure_session_context(session_id or "default", session_token)
    if not confirm:
        raise APIError(
            400,
            "confirmation_required",
            "Pass ?confirm=1 to clear all feedback.",
        )
    _ensure_db()
    try:
        removed = repos.feedback.delete_all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Clearing feedback failed: %s", exc)
        removed = 0
    return {"cleared": removed}


def _row_to_dict(row) -> dict:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "question": row.question,
        "answer": row.answer,
        "score": row.score,
        "comment": row.comment,
        "path_used": row.path_used,
        "model_used": row.model_used,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }