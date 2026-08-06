"""Routes for background /tasks jobs."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from jarvis.api.schemas.chat import TaskCreateRequest, TaskStatusResponse
from jarvis.guardrails.input_guard import validate_input
from jarvis.tasks.runner import get_status, submit_task

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", response_model=TaskStatusResponse)
def create_task(payload: TaskCreateRequest) -> TaskStatusResponse:
    is_valid, error = validate_input(payload.description)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    state = {
        "user_input": payload.description,
        "session_id": payload.session_id or "default",
        "history": [],
        "selected_text": "",
        "fallback_count": 0,
        "show_reasoning": False,
        "answer_style": "",
        "as_background_task": True,
    }
    task_id = submit_task(
        payload.description, session_id=payload.session_id, state=state
    )
    status = get_status(task_id)
    assert status is not None
    return TaskStatusResponse(
        id=status["id"],
        status=status["status"],
        description=status["description"],
        result=status.get("result"),
        error=status.get("error"),
    )


@router.get("/{task_id}", response_model=TaskStatusResponse)
def get_task(task_id: str) -> TaskStatusResponse:
    status = get_status(task_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskStatusResponse(
        id=status["id"],
        status=status["status"],
        description=status["description"],
        result=status.get("result"),
        error=status.get("error"),
    )
