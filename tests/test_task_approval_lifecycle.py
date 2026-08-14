"""Tests for the /tasks approval lifecycle (wait → approve/deny/cancel).

The stub graph returns ``approval_required=True`` on the first invocation
and a final answer after the worker resumes it with ``approved=True``.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from jarvis.api import routes
from jarvis.api.main import app
from jarvis.observability import clear_traces
from jarvis.persistence import create_all, repos
from jarvis.persistence.engine import reset_engine_for_tests
from jarvis.tasks import runner as tasks_runner


@pytest.fixture
def client(monkeypatch):
    from jarvis.security import ratelimit as ratelimit_module

    reset_engine_for_tests()
    create_all()
    monkeypatch.setattr(tasks_runner, "jarvis_graph", _ApprovingGraph())
    routes.chat._sessions.clear()
    routes.chat._pending_approvals.clear()
    clear_traces()
    ratelimit_module.reload_limiter()
    yield TestClient(app)
    # Wake any worker threads still parked on an approval/cancel event so
    # the executor can shut down promptly (no 300s waits between tests).
    for ev in list(tasks_runner._approval_events.values()):
        ev.set()
    for ev in list(tasks_runner._cancel_events.values()):
        ev.set()
    tasks_runner.shutdown()


class _ApprovingGraph:
    """Returns one approval pause, then completes on the resumed call."""

    def invoke(self, state, **kwargs):
        if state.get("approved"):
            state["final_response"] = "file written after approval"
            state["selected_path"] = "coding"
            state["selected_model"] = "qwen2.5-coder:7b"
            state.setdefault("approval_required", False)
        else:
            state["final_response"] = ""
            state["approval_required"] = True
            state["approval_id"] = "appv-task"
            state["approval_expires_at"] = (
                datetime.now(timezone.utc) + timedelta(minutes=5)
            ).isoformat()
            state["risk_level"] = "medium"
            state["pending_action"] = "write_file -> C:/tmp/out.txt"
            state["pending_tool_calls"] = [
                {"name": "write_file", "args": {"file_path": "C:/tmp/out.txt"}}
            ]
        state.setdefault("tools_used", [])
        state.setdefault("sources", [])
        state.setdefault("intent", "coding")
        state.setdefault("complexity", "easy")
        return state


def _poll_status(client, task_id, want=("completed", "failed", "cancelled"), timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/tasks/{task_id}").json()
        if body["status"] in want:
            return body
        time.sleep(0.05)
    return None


def test_task_pauses_for_approval(client):
    r = client.post("/tasks", json={"description": "write a config file"})
    task_id = r.json()["id"]
    body = _poll_status(client, task_id, want=("waiting_for_approval",))
    assert body is not None
    assert body["status"] == "waiting_for_approval"
    assert body["approval_id"] == "appv-task"
    assert body["pending_tool_calls"]


def test_approve_resumes_task_to_completed(client):
    r = client.post("/tasks", json={"description": "write a config file"})
    task_id = r.json()["id"]
    body = _poll_status(client, task_id, want=("waiting_for_approval",))
    assert body is not None

    a = client.post(f"/tasks/{task_id}/approve")
    assert a.status_code == 200
    assert a.json()["status"] == "waiting_for_approval"

    done = _poll_status(client, task_id)
    assert done is not None
    assert done["status"] == "completed"
    assert "file written after approval" in done["result"]

    # The durable approval row is marked approved.
    row = repos.approvals.get("appv-task")
    assert row is not None
    assert row.status == "approved"


def test_deny_marks_task_cancelled(client):
    r = client.post("/tasks", json={"description": "write a config file"})
    task_id = r.json()["id"]
    body = _poll_status(client, task_id, want=("waiting_for_approval",))
    assert body is not None

    d = client.post(f"/tasks/{task_id}/deny")
    assert d.status_code == 200

    done = _poll_status(client, task_id)
    assert done is not None
    assert done["status"] == "cancelled"
    assert "denied" in done["error"]


def test_cancel_while_waiting_is_cancelled(client):
    r = client.post("/tasks", json={"description": "write a config file"})
    task_id = r.json()["id"]
    body = _poll_status(client, task_id, want=("waiting_for_approval",))
    assert body is not None

    c = client.post(f"/tasks/{task_id}/cancel")
    assert c.status_code == 200

    done = _poll_status(client, task_id)
    assert done is not None
    assert done["status"] == "cancelled"


def test_completed_task_cannot_be_approved(client):
    # A non-approval graph → completes instantly, approve must 409.
    import tests.test_tasks_route as tr

    tasks_runner.jarvis_graph = tr._StubGraph()
    r = client.post("/tasks", json={"description": "quick task"})
    task_id = r.json()["id"]
    _poll_status(client, task_id)
    a = client.post(f"/tasks/{task_id}/approve")
    assert a.status_code == 409
    assert a.json()["error"] == "task_not_awaiting_approval"


def test_approve_missing_task_404(client):
    r = client.post("/tasks/does-not-exist/approve")
    assert r.status_code == 404


def test_approval_durable_after_run_within_session(client):
    """Deny path writes a durable row that progressed pending → denied."""
    r = client.post("/tasks", json={"description": "write a config file"})
    task_id = r.json()["id"]
    body = _poll_status(client, task_id, want=("waiting_for_approval",))
    assert body is not None
    approval_id = body["approval_id"]

    client.post(f"/tasks/{task_id}/deny")
    _poll_status(client, task_id)

    row = repos.approvals.get(approval_id)
    assert row is not None
    assert row.status == "denied"