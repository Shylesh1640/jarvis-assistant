"""Tests for Phase 6 feedback: persistence, routes, and the CLI report."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jarvis.api import routes
from jarvis.api.main import app
from jarvis.cli import evaluate as ev
from jarvis.persistence import create_all, repos
from jarvis.persistence.engine import reset_engine_for_tests


@pytest.fixture
def fresh_db(monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.postgres_dsn", "")
    monkeypatch.setattr("jarvis.config.settings.settings.sqlite_path", ":memory:")
    reset_engine_for_tests()
    create_all()
    yield
    reset_engine_for_tests()


@pytest.fixture
def client(fresh_db):
    routes.chat._sessions.clear()
    routes.chat._pending_approvals.clear()
    return TestClient(app)


# ---------------------------------------------------------------------------
# Persistence repo
# ---------------------------------------------------------------------------


def test_feedback_repo_roundtrip(fresh_db):
    fid = repos.feedback.add(
        "s1",
        question="what is RAG?",
        answer="RAG is...",
        score="good",
        comment="nice",
        path_used="general",
        model_used="qwen3:8b",
    )
    rows = repos.feedback.list()
    assert len(rows) == 1
    assert rows[0].score == "good"
    assert rows[0].comment == "nice"
    assert repos.feedback.count() == 1

    per_session = repos.feedback.list_for_session("s1")
    assert len(per_session) == 1

    assert repos.feedback.delete(fid) is True
    assert repos.feedback.delete(fid) is False
    assert repos.feedback.count() == 0


def test_feedback_repo_delete_all(fresh_db):
    repos.feedback.add("s1", question="q", answer="a", score="good")
    repos.feedback.add("s2", question="q", answer="a", score="bad")
    assert repos.feedback.delete_all() == 2
    assert repos.feedback.count() == 0


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def test_feedback_submit_valid(client):
    r = client.post(
        "/feedback",
        json={
            "session_id": "s1",
            "question": "what is RAG?",
            "answer": "Retrieval-augmented...",
            "score": "good",
            "comment": "clear",
            "model_used": "qwen3:8b",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["stored"] is True
    assert body["id"] is not None


def test_feedback_submit_invalid_score(client):
    r = client.post(
        "/feedback",
        json={"answer": "x", "score": "amazing"},
    )
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_score"


def test_feedback_submit_missing_answer(client):
    r = client.post("/feedback", json={"score": "good", "answer": " "})
    assert r.status_code == 400


def test_feedback_list_and_detail(client):
    client.post(
        "/feedback",
        json={"session_id": "s1", "question": "q1", "answer": "a1", "score": "good"},
    )
    r = client.get("/feedback")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["items"][0]["score"] == "good"

    r = client.get("/feedback", params={"session_id": "s1"})
    assert r.json()["count"] == 1
    r = client.get("/feedback", params={"session_id": "other"})
    assert r.json()["count"] == 0


def test_feedback_delete_requires_confirmation(client):
    client.post("/feedback", json={"answer": "a", "score": "bad"})
    r = client.delete("/feedback/1")
    assert r.status_code == 400
    assert r.json()["error"] == "confirmation_required"


def test_feedback_delete_with_confirmation(client):
    r0 = client.post("/feedback", json={"answer": "a", "score": "bad"})
    fid = r0.json()["id"]
    r = client.delete(f"/feedback/{fid}?confirm=1")
    assert r.status_code == 200
    assert r.json()["deleted"] == fid


def test_feedback_delete_missing_404(client):
    r = client.delete("/feedback/9999?confirm=1")
    assert r.status_code == 404


def test_feedback_clear(client):
    client.post("/feedback", json={"answer": "a", "score": "good"})
    client.post("/feedback", json={"answer": "b", "score": "bad"})
    r = client.delete("/feedback?confirm=1")
    assert r.status_code == 200
    assert r.json()["cleared"] == 2


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_evaluate_empty(fresh_db, capsys):
    assert ev.main([]) == 0
    out = capsys.readouterr().out
    assert "No feedback collected yet" in out


def test_evaluate_report_and_detail(fresh_db, capsys):
    repos.feedback.add("s1", question="q", answer="a", score="good", model_used="m1")
    repos.feedback.add("s1", question="q", answer="a", score="bad", model_used="m1")
    assert ev.main(["--detail"]) == 0
    out = capsys.readouterr().out
    assert "Total entries: 2" in out
    assert "bad" in out
    assert "rated bad" in out
    assert "Entries (2 shown)" in out


def test_evaluate_score_filter(fresh_db, capsys):
    repos.feedback.add("s1", question="q", answer="a", score="good")
    repos.feedback.add("s1", question="q", answer="a", score="bad")
    assert ev.main(["--score", "bad"]) == 0
    out = capsys.readouterr().out
    assert "Entries (1 shown)" in out


def test_evaluate_clear(fresh_db, capsys):
    repos.feedback.add("s1", question="q", answer="a", score="good")
    assert ev.main(["--clear"]) == 0
    out = capsys.readouterr().out
    assert "Cleared 1" in out
    assert repos.feedback.count() == 0