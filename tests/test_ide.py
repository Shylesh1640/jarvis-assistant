"""Tests for Phase 8 :: IDE integration.

Covers the /ide routes (confirm-gated, workspace-confined, structured
"not configured" when disabled) and the workspace executor (path escaping is
rejected). Commands and test runs are monkeypatched with fakes — nothing is
executed for real.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jarvis.api import routes
from jarvis.api.main import app
from jarvis.ide.executor import (
    IDEUnconfiguredError,
    OutsideWorkspaceError,
    resolve_in_workspace,
)
from jarvis.persistence import create_all
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
def ide_on(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.ide_integration_enabled", True)
    monkeypatch.setattr("jarvis.config.settings.settings.ide_workspace_root", str(tmp_path))
    return tmp_path


@pytest.fixture
def client(fresh_db):
    routes.chat._sessions.clear()
    routes.chat._pending_approvals.clear()
    return TestClient(app)


# ---------------------------------------------------------------------------
# Workspace executor
# ---------------------------------------------------------------------------


def test_resolve_in_workspace(ide_on):
    (ide_on / "a.txt").write_text("hi", encoding="utf-8")
    assert resolve_in_workspace("a.txt").name == "a.txt"
    with pytest.raises(OutsideWorkspaceError):
        resolve_in_workspace("../escape.txt")


def test_require_workspace_unconfigured(fresh_db):
    with pytest.raises(IDEUnconfiguredError):
        resolve_in_workspace("a.txt")


# ---------------------------------------------------------------------------
# Routes — not configured
# ---------------------------------------------------------------------------


def test_ide_routes_not_configured(client):
    r = client.post("/ide/execute-command", json={"command": "dir"}, params={"confirm": 1})
    assert r.status_code == 503
    assert r.json()["error"] == "ide_not_configured"
    r = client.post("/ide/open-file", json={"path": "a.txt"}, params={"confirm": 1})
    assert r.status_code == 503
    assert r.json()["error"] == "ide_not_configured"


# ---------------------------------------------------------------------------
# Routes — confirm gate
# ---------------------------------------------------------------------------


def test_ide_routes_need_confirm(client, ide_on):
    for path, payload in [
        ("/ide/execute-command", {"command": "dir"}),
        ("/ide/open-file", {"path": "a.txt"}),
        ("/ide/search-files", {"pattern": "*.txt"}),
        ("/ide/run-tests", {}),
    ]:
        r = client.post(path, json=payload)
        assert r.status_code == 400, path
        assert r.json()["error"] == "confirmation_required", path


# ---------------------------------------------------------------------------
# Routes — configured (executor faked for command/tests)
# ---------------------------------------------------------------------------


def test_ide_open_and_search(client, ide_on, monkeypatch):
    (ide_on / "hello.txt").write_text("hello world", encoding="utf-8")
    (ide_on / "sub").mkdir()
    (ide_on / "sub" / "nested.py").write_text("x = 1", encoding="utf-8")

    opened = client.post("/ide/open-file", json={"path": "hello.txt"}, params={"confirm": 1})
    assert opened.status_code == 200
    assert opened.json()["content"] == "hello world"
    assert ".." not in opened.json()["path"] or opened.json()["path"].startswith(str(ide_on))

    searched = client.post("/ide/search-files", json={"pattern": "**/*.py"}, params={"confirm": 1})
    assert searched.status_code == 200
    assert searched.json()["files"][0].endswith("nested.py")

    escaped = client.post("/ide/open-file", json={"path": "../outside.txt"}, params={"confirm": 1})
    assert escaped.status_code == 403
    assert escaped.json()["error"] == "outside_workspace"


def test_ide_execute_and_tests(client, ide_on, monkeypatch):
    monkeypatch.setattr(
        "jarvis.api.routes.ide.execute_command",
        lambda cmd: {"ok": True, "returncode": 0, "output": f"ran {cmd}"},
    )
    monkeypatch.setattr(
        "jarvis.api.routes.ide.run_tests",
        lambda: {"ok": True, "returncode": 0, "output": "1 passed"},
    )
    r = client.post("/ide/execute-command", json={"command": "echo hi"}, params={"confirm": 1})
    assert r.status_code == 200
    assert r.json()["output"] == "ran echo hi"

    r = client.post("/ide/run-tests", json={}, params={"confirm": 1})
    assert r.status_code == 200
    assert r.json()["output"] == "1 passed"