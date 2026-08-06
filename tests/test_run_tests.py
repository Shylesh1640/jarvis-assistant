"""Tests for the ``run_tests`` tool.

Uses a tiny synthetic test tree under the workspace so we exercise the
happy path and the error paths without depending on the repo's own
pytest suite (which would be slow and create a feedback loop).
"""
import pytest

from jarvis.tools.coding.run_tests import _summarize, run_tests


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(
        "jarvis.tools.coding.paths.settings.workspace_dir", str(ws)
    )
    monkeypatch.setattr(
        "jarvis.tools.coding.run_tests.settings.tool_subprocess_timeout", 60
    )
    return ws


def test_run_tests_reports_pass(workspace):
    (workspace / "test_pass.py").write_text(
        "def test_ok():\n    assert 1 + 1 == 2\n", encoding="utf-8"
    )
    out = run_tests.invoke({"target": "."})
    assert "exit=0" in out
    assert "passed" in out or "1 passed" in out or "passed" in out.lower()


def test_run_tests_reports_fail(workspace):
    (workspace / "test_fail.py").write_text(
        "def test_bad():\n    assert 1 == 2\n", encoding="utf-8"
    )
    out = run_tests.invoke({"target": "."})
    assert "exit=" in out
    # Non-zero exit code on failure.
    assert "exit=0" not in out


def test_run_tests_missing_target_error(workspace):
    out = run_tests.invoke({"target": "does_not_exist"})
    assert out.startswith("Error")
    assert "does not exist" in out


def test_run_tests_path_escape_rejected(workspace, tmp_path):
    out = run_tests.invoke({"target": str(tmp_path.parent)})
    assert out.startswith("Error")


def test_summarize_extracts_status_line():
    sample = "collected 5 items\n....F\n======================================================== short test summary ========================================================\nFAILED test_x.py::test_bad - assert 1 == 2\n========================= 4 passed, 1 failed in 0.05s ========================="
    out = _summarize(sample)
    assert "4 passed, 1 failed" in out


def test_summarize_empty_input():
    assert _summarize("") == ""
