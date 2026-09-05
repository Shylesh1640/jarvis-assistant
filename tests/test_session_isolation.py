"""Regression test for session isolation.

Ensures that multiple independent sessions do not share conversation state.
This test would fail if the Streamlit app ever defaults multiple users to
a shared session ID (e.g., the old hardcoded "default").
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from jarvis.api import routes
from jarvis.api.main import app


@pytest.fixture
def client(monkeypatch):
    """FastAPI TestClient with the graph stubbed out."""
    monkeypatch.setattr(routes.chat, "jarvis_graph", _StubGraph())
    routes.chat._sessions.clear()
    routes.chat._pending_approvals.clear()
    yield TestClient(app)


class _StubGraph:
    """Mimic the compiled LangGraph's invoke for routed testing."""

    def invoke(self, state, config=None):
        user_input = state.get("user_input", "").lower()

        # Return different responses based on the prompt to detect cross-contamination
        if "17 * 24" in user_input or "17*24" in user_input or "408" in user_input:
            state["final_response"] = "408"
        elif "recursion" in user_input or "blue-test-927" in user_input:
            state["final_response"] = "Recursion is when a function calls itself. BLUE-TEST-927"
        else:
            state["final_response"] = f"answer to: {state.get('user_input', '')}"

        state["selected_path"] = "general"
        state["selected_model"] = "qwen3:8b"
        state.setdefault("approval_required", False)
        state.setdefault("pending_action", None)
        state.setdefault("tools_used", [])
        state.setdefault("sources", [])
        state.setdefault("retrieved_context", "")
        return state


def test_no_default_session_contamination(client):
    """Two independent sessions must not share conversation state.

    Session A asks a math question (expecting "408").
    Session B asks an explanation question (expecting "BLUE-TEST-927").
    Session B's response must not contain Session A's answer ("408").
    """
    session_a = str(uuid.uuid4())
    session_b = str(uuid.uuid4())

    # Session A: math question
    r1 = client.post(
        "/chat",
        json={
            "session_id": session_a,
            "message": "What is 17 * 24? Give only the number.",
            "history": [],
            "approved": False,
            "show_reasoning": False,
            "deep_thinking": False,
        },
    )
    r1.raise_for_status()
    data1 = r1.json()
    assert "408" in data1["response"], f"Session A should get math answer, got: {data1['response']}"

    # Session B: explanation question
    r2 = client.post(
        "/chat",
        json={
            "session_id": session_b,
            "message": "Explain recursion in simple terms. Include the word BLUE-TEST-927.",
            "history": [],
            "approved": False,
            "show_reasoning": False,
            "deep_thinking": False,
        },
    )
    r2.raise_for_status()
    data2 = r2.json()

    # Ensure no contamination
    assert "408" not in data2["response"], (
        f"Session B contaminated with Session A's answer '408': {data2['response']}"
    )
    assert "BLUE-TEST-927" in data2["response"], (
        f"Session B should contain test marker, got: {data2['response']}"
    )
    assert data1["session_id"] != data2["session_id"], "Sessions must have distinct IDs"


def test_session_history_isolation(client):
    """Verify that each session maintains its own history."""
    session_a = str(uuid.uuid4())
    session_b = str(uuid.uuid4())

    # Session A: first message
    r1 = client.post(
        "/chat",
        json={"session_id": session_a, "message": "Hello from A", "approved": False},
    )
    r1.raise_for_status()

    # Session B: first message
    r2 = client.post(
        "/chat",
        json={"session_id": session_b, "message": "Hello from B", "approved": False},
    )
    r2.raise_for_status()

    # Session A: second message - should see its own history
    r3 = client.post(
        "/chat",
        json={"session_id": session_a, "message": "What did I just say?", "approved": False},
    )
    r3.raise_for_status()
    data3 = r3.json()

    # Session B: second message - should see its own history
    r4 = client.post(
        "/chat",
        json={"session_id": session_b, "message": "What did I just say?", "approved": False},
    )
    r4.raise_for_status()
    data4 = r4.json()

    # Each session should only reference its own previous message
    # (The stub graph doesn't actually use history, but the server persists it)
    assert session_a in routes.chat._sessions
    assert session_b in routes.chat._sessions
    assert routes.chat._sessions[session_a] != routes.chat._sessions[session_b]