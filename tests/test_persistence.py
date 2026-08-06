"""Tests for the persistence layer (SQLAlchemy repos).

Uses an in-memory SQLite engine swapped in via ``reset_engine_for_tests``
so the suite never touches a real Postgres or a disk file. Each test gets
a fresh schema (tables are created after the engine reset).
"""
import pytest

from jarvis.persistence import create_all, repos
from jarvis.persistence.engine import reset_engine_for_tests


@pytest.fixture(autouse=True)
def fresh_db():
    reset_engine_for_tests()
    create_all()
    yield
    reset_engine_for_tests()


# ---------------------------------------------------------------------------
# sessions + messages
# ---------------------------------------------------------------------------


def test_get_or_create_session():
    repos.sessions.get_or_create("s1")
    # Second call is a no-op.
    repos.sessions.get_or_create("s1")
    assert repos.messages.count_for_session("s1") == 0


def test_add_message_returns_id():
    mid = repos.messages.add("s1", role="user", content="hi")
    assert isinstance(mid, int)
    assert repos.messages.count_for_session("s1") == 1


def test_history_preserves_order_and_metadata():
    mid1 = repos.messages.add("s1", role="user", content="hi")
    mid2 = repos.messages.add(
        "s1",
        role="assistant",
        content="hello",
        path_used="general",
        model_used="qwen3:8b",
        tools_used=["calculator"],
        sources=[{"source": "docs/x.md", "chunk_id": "1", "doc": "..."}],
    )
    history = repos.messages.history("s1")
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "hi"
    assert history[1]["pathUsed"] == "general"
    assert history[1]["modelUsed"] == "qwen3:8b"
    assert history[1]["toolsUsed"] == ["calculator"]
    assert history[1]["sources"][0]["source"] == "docs/x.md"


def test_count_for_session_empty():
    assert repos.messages.count_for_session("ghost") == 0


def test_tail_returns_last_n():
    for i in range(10):
        repos.messages.add("s1", role="user", content=f"u{i}")
        repos.messages.add("s1", role="assistant", content=f"a{i}")
    tail = repos.messages.tail("s1", limit=4)
    assert len(tail) == 4
    assert tail[0]["content"] == "u8"
    assert tail[-1]["content"] == "a9"


# ---------------------------------------------------------------------------
# summaries
# ---------------------------------------------------------------------------


def test_summary_add_and_count():
    repos.summaries.add("s1", summary="bullet 1")
    assert repos.summaries.count_for_session("s1") == 1
    latest = repos.summaries.latest_for_session("s1")
    assert latest is not None
    assert latest.summary == "bullet 1"


def test_summary_latest_none_for_unknown_session():
    assert repos.summaries.latest_for_session("ghost") is None


# ---------------------------------------------------------------------------
# approvals
# ---------------------------------------------------------------------------


def test_approval_put_get_pop():
    repos.approvals.put("s1", state={"x": 1}, pending_action="run_shell('ls')")
    row = repos.approvals.get("s1")
    assert row is not None
    assert row.state["x"] == 1
    assert row.pending_action == "run_shell('ls')"

    popped = repos.approvals.pop("s1")
    assert popped is not None
    assert repos.approvals.get("s1") is None
    second = repos.approvals.pop("s1")
    assert second is None


def test_approval_clear_idempotent():
    repos.approvals.clear("s1")  # no row -> no error
    repos.approvals.put("s1", state={"a": 1}, pending_action=None)
    repos.approvals.clear("s1")
    repos.approvals.clear("s1")  # idempotent


# ---------------------------------------------------------------------------
# tasks
# ---------------------------------------------------------------------------


def test_task_create_get_mark_done():
    repos.tasks.create("t1", description="design X", session_id="s1")
    row = repos.tasks.get("t1")
    assert row is not None
    assert row.status == "pending"

    repos.tasks.mark_running("t1")
    assert repos.tasks.get("t1").status == "running"

    repos.tasks.mark_done("t1", result="done")
    done = repos.tasks.get("t1")
    assert done.status == "completed"
    assert done.result == "done"
    assert done.finished_at is not None


def test_task_mark_failed():
    repos.tasks.create("t2", description="boom")
    repos.tasks.mark_failed("t2", error="kaboom")
    row = repos.tasks.get("t2")
    assert row.status == "failed"
    assert row.error == "kaboom"


def test_task_get_missing_returns_none():
    assert repos.tasks.get("does-not-exist") is None
