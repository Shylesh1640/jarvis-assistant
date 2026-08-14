"""Background task execution for long-running prompts.

A small in-process ``ThreadPoolExecutor`` runs graph invocations
asynchronously. Each task is persisted via :mod:`jarvis.persistence`
(TaskRepo) with a full lifecycle::

    queued -> running -> completed
                       -> waiting_for_approval -> completed
                                               -> cancelled
                       -> failed
                       -> cancelled

Tasks that need a risky tool action **pause** in ``waiting_for_approval``
instead of auto-approving, exactly like interactive requests. The pending
approval is persisted to the durable approvals table and the worker thread
blocks on a ``threading.Event`` until the user approves / denies / cancels
(or the approval TTL expires). ``POST /tasks/{id}/approve`` and
``POST /tasks/{id}/cancel`` drive that decision.
"""
from __future__ import annotations

import logging
import secrets
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from jarvis.guardrails.output_guard import redact_output
from jarvis.observability.trace import finish_trace, new_trace, trace_event
from jarvis.orchestration.graph import jarvis_graph
from jarvis.persistence import create_all, repos
from jarvis.persistence.state_codec import state_from_json, state_to_json

logger = logging.getLogger("jarvis.tasks")

_MAX_WORKERS = 4
_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()
_futures: dict[str, Future] = {}
# task_id -> threading.Event (set => cancellation requested)
_cancel_events: dict[str, threading.Event] = {}
# approval_id -> threading.Event (set => user decided)
_approval_events: dict[str, threading.Event] = {}
_registry_lock = threading.Lock()


def _ensure_db() -> None:
    try:
        create_all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("create_all failed: %s", exc)


def get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        with _executor_lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(
                    max_workers=_MAX_WORKERS,
                    thread_name_prefix="jarvis-task",
                )
    return _executor


def _cancel_requested(task_id: str) -> bool:
    ev = _cancel_events.get(task_id)
    return ev is not None and ev.is_set()


def submit_task(description: str, *, session_id: str | None = None, state: dict) -> str:
    """Schedule *description* for background execution; return the task id."""
    _ensure_db()
    task_id = secrets.token_hex(8)
    sid = session_id or "default"
    repos.tasks.create(task_id, description=description, session_id=sid)
    with _registry_lock:
        _cancel_events[task_id] = threading.Event()
    fut = get_executor().submit(_run, task_id, sid, description, state)
    _futures[task_id] = fut
    logger.info("Submitted task %s for session %s", task_id, sid)
    return task_id


def cancel_task(task_id: str) -> dict:
    """Request cancellation of *task_id*. Returns the updated status dict."""
    row = repos.tasks.get(task_id)
    if row is None:
        raise KeyError(task_id)

    if row.status in ("completed", "failed", "cancelled"):
        return _row_to_status(row)

    if row.status == "waiting_for_approval":
        if row.approval_id:
            repos.approvals.set_status(row.approval_id, "cancelled")
            with _registry_lock:
                ev = _approval_events.get(row.approval_id)
            if ev is not None:
                ev.set()

    with _registry_lock:
        ev = _cancel_events.get(task_id)
    if ev is not None:
        ev.set()

    if row.status == "queued":
        repos.tasks.mark_cancelled(task_id, "cancelled before start")
    elif row.status == "running":
        repos.tasks.update_stage(task_id, "cancelling…")
    return _row_to_status(repos.tasks.get(task_id))


def approve_task(task_id: str) -> dict:
    """Approve a task waiting for approval; resumes the worker thread."""
    row = repos.tasks.get(task_id)
    if row is None:
        raise KeyError(task_id)
    if row.status != "waiting_for_approval":
        raise ValueError(row.status)
    if row.approval_id:
        repos.approvals.set_status(row.approval_id, "approved")
        with _registry_lock:
            ev = _approval_events.get(row.approval_id)
        if ev is not None:
            ev.set()
    return _row_to_status(repos.tasks.get(task_id))


def deny_task(task_id: str) -> dict:
    """Deny a task waiting for approval; marks the task cancelled."""
    row = repos.tasks.get(task_id)
    if row is None:
        raise KeyError(task_id)
    if row.status != "waiting_for_approval":
        raise ValueError(row.status)
    if row.approval_id:
        repos.approvals.set_status(row.approval_id, "denied")
        with _registry_lock:
            ev = _approval_events.get(row.approval_id)
        if ev is not None:
            ev.set()
    return _row_to_status(repos.tasks.get(task_id))


def _run(task_id: str, session_id: str, description: str, state: dict) -> None:
    trace = new_trace(session_id=session_id)
    try:
        repos.tasks.mark_running(task_id)
        repos.tasks.update_stage(task_id, "planning…")
        trace_event(trace, "task_running")

        state["as_background_task"] = True
        if _cancel_requested(task_id):
            repos.tasks.mark_cancelled(task_id, "cancelled before start")
            finish_trace(trace)
            return

        result = jarvis_graph.invoke(state)

        if result.get("approval_required"):
            _wait_for_approval(task_id, session_id, result, trace)
            return

        if _cancel_requested(task_id):
            repos.tasks.mark_cancelled(task_id, "cancelled")
            trace_event(trace, "task_cancelled")
            finish_trace(trace)
            return

        repos.tasks.update_stage(task_id, "finalising…")
        text = redact_output(result.get("final_response", "") or "")
        repos.tasks.mark_done(task_id, text)
        trace_event(
            trace,
            "task_completed",
            intent=result.get("intent"),
            model=result.get("selected_model"),
        )
        finish_trace(trace, result=result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("task %s failed", task_id)
        repos.tasks.mark_failed(task_id, str(exc))
        trace_event(trace, "task_failed", error=str(exc))
        finish_trace(trace)
    finally:
        with _registry_lock:
            _cancel_events.pop(task_id, None)
            _futures.pop(task_id, None)


def _wait_for_approval(
    task_id: str, session_id: str, result: dict, trace
) -> None:
    """Persist the pending approval, pause the worker, resume on decision.

    The graph result is the full paused state (messages include the exact
    tool call(s) awaiting permission). We serialise it into the durable
    approvals table so the decision (approve/deny/cancel/expire) can be
    applied even after a backend restart — though after a restart the worker
    thread is gone and the task is swept to ``failed`` at startup.
    """
    approval_id = result.get("approval_id")
    tool_calls = list(result.get("pending_tool_calls", []) or [])
    serialized = state_to_json(result)

    approval_id = _persist_approval(task_id, session_id, result, tool_calls, serialized, approval_id)

    repos.tasks.mark_waiting_for_approval(
        task_id,
        approval_id=approval_id,
        pending_action=result.get("pending_action"),
        pending_tool_calls=tool_calls,
    )
    trace_event(trace, "task_waiting_approval", approval_id=approval_id)

    event = threading.Event()
    with _registry_lock:
        _approval_events[approval_id] = event

    wait_seconds = _ttl_remaining(result.get("approval_expires_at")) or 300
    event.wait(timeout=wait_seconds)

    with _registry_lock:
        _approval_events.pop(approval_id, None)

    if _cancel_requested(task_id):
        repos.tasks.mark_cancelled(task_id, "cancelled while awaiting approval")
        finish_trace(trace)
        return

    row = repos.approvals.get(approval_id)
    status = row.status if row else "expired"

    if status == "approved":
        resume_state = state_from_json(row.state)
        resume_state["approved"] = True
        try:
            resumed = jarvis_graph.invoke(
                resume_state,
                config={"configurable": {"thread_id": f"jarvis-task:{task_id}"}},
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("task %s resume failed after approval", task_id)
            repos.tasks.mark_failed(task_id, f"resume failed: {exc}")
            trace_event(trace, "task_resume_failed", error=str(exc))
            finish_trace(trace)
            return
        text = redact_output(resumed.get("final_response", "") or "")
        repos.tasks.mark_done(task_id, text)
        trace_event(
            trace,
            "task_completed_after_approval",
            intent=resumed.get("intent"),
            model=resumed.get("selected_model"),
        )
        finish_trace(trace, result=resumed)
    elif status == "denied":
        repos.tasks.mark_cancelled(task_id, "denied by user")
        trace_event(trace, "task_denied")
        finish_trace(trace)
    else:
        repos.tasks.mark_failed(task_id, "approval expired or unavailable")
        trace_event(trace, "task_approval_expired")
        finish_trace(trace)


def _persist_approval(
    task_id: str, session_id: str, result: dict, tool_calls: list, serialized: dict, approval_id: str | None
) -> str:
    approval_id = approval_id or secrets.token_hex(16)
    try:
        repos.approvals.create(
            approval_id,
            session_id=session_id,
            state=serialized,
            expires_at=_parse_expiry(result.get("approval_expires_at")),
            tool_name=tool_calls[0].get("name") if tool_calls else None,
            arguments=tool_calls[0].get("args") if tool_calls else None,
            tool_calls=tool_calls,
            risk_level=result.get("risk_level", "medium"),
            pending_action=result.get("pending_action"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not persist approval for task %s: %s", task_id, exc)
    return approval_id


def _parse_expiry(raw: str | None):
    if not raw:
        return datetime.now(timezone.utc) + timedelta(seconds=300)
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return datetime.now(timezone.utc) + timedelta(seconds=300)


def _ttl_remaining(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, int((dt - datetime.now(timezone.utc)).total_seconds()))
    except ValueError:
        return None


def get_status(task_id: str) -> dict | None:
    row = repos.tasks.get(task_id)
    if row is None:
        return None
    return _row_to_status(row)


def _row_to_status(row) -> dict:
    return {
        "id": row.id,
        "status": row.status,
        "description": row.description,
        "stage": row.stage,
        "result": row.result,
        "error": row.error,
        "approval_id": row.approval_id,
        "pending_action": row.pending_action,
        "pending_tool_calls": list(row.pending_tool_calls or []),
        "session_id": row.session_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


def recover_stale_tasks() -> int:
    """Fail tasks orphaned by a previous process (called once at startup)."""
    try:
        return repos.tasks.recover_stale()
    except Exception as exc:  # noqa: BLE001
        logger.warning("recover_stale_tasks failed: %s", exc)
        return 0


def shutdown() -> None:
    """Best-effort graceful shutdown — used by tests."""
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=True, cancel_futures=True)
        _executor = None
    with _registry_lock:
        _futures.clear()
        _cancel_events.clear()
        _approval_events.clear()


__all__ = [
    "submit_task",
    "cancel_task",
    "approve_task",
    "deny_task",
    "get_status",
    "recover_stale_tasks",
    "shutdown",
]
