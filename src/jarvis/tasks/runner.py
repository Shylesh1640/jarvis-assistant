"""Background task execution for long-running prompts.

A small in-process ``ThreadPoolExecutor`` runs graph invocations
asynchronously. Each task is persisted via :mod:`jarvis.persistence`
(TaskRepo) with status transitions pending -> running ->
completed/failed. The executor is bounded (a handful of workers) which
is plenty for a local-first assistant; for higher throughput, swap the
executor for a queue + worker process.
"""
from __future__ import annotations

import logging
import secrets
import threading
from concurrent.futures import Future, ThreadPoolExecutor

from jarvis.guardrails.output_guard import redact_output
from jarvis.observability.trace import finish_trace, new_trace, trace_event
from jarvis.orchestration.graph import jarvis_graph
from jarvis.persistence import create_all, repos

logger = logging.getLogger("jarvis.tasks")

_MAX_WORKERS = 4
_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()
_futures: dict[str, Future] = {}


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


def _run(task_id: str, session_id: str, description: str, state: dict) -> None:
    trace = new_trace(session_id=session_id)
    try:
        repos.tasks.mark_running(task_id)
        trace_event(trace, "task_running")
        # Background jobs always run with approval disabled — there is no
        # interactive user to approve, and the coding toolset already
        # restricts writes to the workspace and shell to an allowlist.
        state["as_background_task"] = True
        state["approved"] = True
        result = jarvis_graph.invoke(state)
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


def submit_task(description: str, *, session_id: str | None = None, state: dict) -> str:
    """Schedule *description* for background execution; return the task id."""
    _ensure_db()
    task_id = secrets.token_hex(8)
    sid = session_id or "default"
    repos.tasks.create(task_id, description=description, session_id=sid)
    fut = get_executor().submit(_run, task_id, sid, description, state)
    _futures[task_id] = fut
    logger.info("Submitted task %s for session %s", task_id, sid)
    return task_id


def get_status(task_id: str) -> dict | None:
    row = repos.tasks.get(task_id)
    if row is None:
        return None
    return {
        "id": row.id,
        "status": row.status,
        "description": row.description,
        "result": row.result,
        "error": row.error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


def shutdown() -> None:
    """Best-effort graceful shutdown — used by tests."""
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=True, cancel_futures=True)
        _executor = None
    _futures.clear()
