"""Routes for background /tasks jobs.

Lifecycle: ``queued -> running -> completed/failed/cancelled`` with an
optional ``waiting_for_approval`` pause. Tasks awaiting approval are
resolved through ``POST /tasks/{id}/approve`` and ``POST /tasks/{id}/deny``.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from jarvis.api.errors import APIError
from jarvis.api.schemas.chat import (
    TaskApprovalRequest,
    TaskCreateRequest,
    TaskStatusResponse,
)
from jarvis.guardrails.input_guard import validate_input
from jarvis.security.ratelimit import rate_limited
from jarvis.security.session_auth import ensure_session_context
from jarvis.tasks.runner import (
    approve_task,
    cancel_task,
    deny_task,
    get_status,
    submit_task,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _to_response(status: dict) -> TaskStatusResponse:
    return TaskStatusResponse(
        id=status["id"],
        status=status["status"],
        description=status["description"],
        stage=status.get("stage"),
        result=status.get("result"),
        error=status.get("error"),
        approval_id=status.get("approval_id"),
        pending_action=status.get("pending_action"),
        pending_tool_calls=list(status.get("pending_tool_calls") or []),
        session_id=status.get("session_id"),
        created_at=status.get("created_at"),
        started_at=status.get("started_at"),
        finished_at=status.get("finished_at"),
    )


@router.post("", response_model=TaskStatusResponse)
def create_task(payload: TaskCreateRequest, request: Request) -> TaskStatusResponse:
    rate_limited(request, payload.session_id)

    is_valid, error = validate_input(payload.description)
    if not is_valid:
        raise APIError(400, "invalid_input", error or "Invalid input.")

    session_id = payload.session_id or "default"
    ensure_session_context(session_id, payload.session_token)

    state = {
        "user_input": payload.description,
        "session_id": session_id,
        "history": [],
        "selected_text": "",
        "fallback_count": 0,
        "show_reasoning": False,
        "answer_style": "",
        "as_background_task": True,
    }
    task_id = submit_task(payload.description, session_id=session_id, state=state)
    status = get_status(task_id)
    assert status is not None
    return _to_response(status)


@router.get("/{task_id}", response_model=TaskStatusResponse)
def get_task(task_id: str) -> TaskStatusResponse:
    status = get_status(task_id)
    if status is None:
        raise APIError(404, "task_not_found", "Task not found.")
    return _to_response(status)


@router.post("/{task_id}/cancel", response_model=TaskStatusResponse)
def cancel_task_route(task_id: str) -> TaskStatusResponse:
    try:
        status = cancel_task(task_id)
    except KeyError:
        raise APIError(404, "task_not_found", "Task not found.") from None
    return _to_response(status)


@router.post("/{task_id}/approve", response_model=TaskStatusResponse)
def approve_task_route(
    task_id: str, payload: TaskApprovalRequest | None = None
) -> TaskStatusResponse:
    del payload  # reserved for future "always allow this tool" semantics
    try:
        status = approve_task(task_id)
    except KeyError:
        raise APIError(404, "task_not_found", "Task not found.") from None
    except ValueError as exc:
        raise APIError(
            409, "task_not_awaiting_approval", f"Task is {exc}; it is not awaiting approval."
        ) from exc
    return _to_response(status)


@router.post("/{task_id}/deny", response_model=TaskStatusResponse)
def deny_task_route(task_id: str) -> TaskStatusResponse:
    try:
        status = deny_task(task_id)
    except KeyError:
        raise APIError(404, "task_not_found", "Task not found.") from None
    except ValueError as exc:
        raise APIError(
            409, "task_not_awaiting_approval", f"Task is {exc}; it is not awaiting approval."
        ) from exc
    return _to_response(status)