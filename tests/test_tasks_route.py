"""Tests for the /tasks API routes.

Uses an in-memory SQLite engine and a stubbed graph so submitting a task
completes synchronously inside the worker thread almost immediately.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from jarvis.api import routes
from jarvis.api.main import app
from jarvis.observability import clear_traces
from jarvis.persistence import create_all
from jarvis.persistence.engine import reset_engine_for_tests
from jarvis.tasks import runner as tasks_runner


@pytest.fixture
def client(monkeypatch):
    reset_engine_for_tests()
    create_all()

    monkeypatch.setattr(routes.chat, "jarvis_graph", _StubGraph())
    monkeypatch.setattr(tasks_runner, "jarvis_graph", _StubGraph())
    routes.chat._sessions.clear()
    routes.chat._pending_approvals.clear()
    clear_traces()
    yield TestClient(app)
    tasks_runner.shutdown()


class _StubGraph:
    def __init__(self) -> None:
        self.invoke_handler = _stable_invoke

    def invoke(self, state):
        return self.invoke_handler(state)


def _stable_invoke(state):
    state["final_response"] = f"bg answer to: {state.get('user_input', '')}"
    state["selected_path"] = "general"
    state["selected_model"] = "qwen3:8b"
    state.setdefault("approval_required", False)
    state.setdefault("pending_action", None)
    state.setdefault("tools_used", [])
    state.setdefault("sources", [])
    state.setdefault("retrieved_context", "")
    return state


def _wait_for(task_id: str, client, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/tasks/{task_id}")
        body = r.json()
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(0.05)
    return None


def test_create_task_returns_pending(client):
    r = client.post("/tasks", json={"description": "design something"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] in ("queued", "running", "completed")
    assert data["id"]


def test_task_completes_with_result(client):
    r = client.post("/tasks", json={"description": "design something"})
    task_id = r.json()["id"]
    body = _wait_for(task_id, client)
    assert body is not None
    assert body["status"] == "completed"
    assert "bg answer to" in body["result"]


def test_task_rejects_empty_description(client):
    r = client.post("/tasks", json={"description": "  "})
    assert r.status_code == 400


def test_get_missing_task_404(client):
    r = client.get("/tasks/does-not-exist")
    assert r.status_code == 404


def test_task_failure_recorded(client, monkeypatch):
    def _boom(state):
        raise RuntimeError("graph exploded")

    routes.chat.jarvis_graph.invoke_handler = _boom
    tasks_runner.jarvis_graph.invoke_handler = _boom
    r = client.post("/tasks", json={"description": "do it"})
    task_id = r.json()["id"]
    body = _wait_for(task_id, client)
    assert body is not None
    assert body["status"] == "failed"
    assert "graph exploded" in body["error"]
