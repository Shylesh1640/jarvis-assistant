"""Tests for Phase 5 planning node + task duration enforcement."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jarvis.orchestration.planning_node import format_plan_block, plan_task
from jarvis.tasks import runner


# ---------------------------------------------------------------------------
# Planning node
# ---------------------------------------------------------------------------


def test_plan_task_skipped_when_disabled(monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.max_plan_steps", 0)
    state = {"user_input": "big request", "intent": "complex"}
    out = plan_task(state)
    assert out["plan"] == []
    assert out["plan_block"] == ""


def test_plan_task_skipped_for_non_complex(monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.max_plan_steps", 8)
    state = {"user_input": "hi", "intent": "general"}
    out = plan_task(state)
    assert out["plan"] == []


def test_plan_task_generates_capped_steps(monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.max_plan_steps", 2)
    resp = MagicMock(content='["step one", "step two", "step three"]')
    monkeypatch.setattr(
        "jarvis.orchestration.planning_node.get_router_model",
        lambda: MagicMock(invoke=lambda p: resp),
    )
    state = {"user_input": "design a system", "intent": "complex"}
    out = plan_task(state)
    assert out["plan"] == ["step one", "step two"]  # capped to 2
    assert "<<<TASK PLAN>>>" in out["plan_block"]
    assert "<<<END TASK PLAN>>>" in out["plan_block"]


def test_plan_task_fails_open(monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.max_plan_steps", 5)

    def _boom(prompt):
        raise RuntimeError("llm down")

    monkeypatch.setattr(
        "jarvis.orchestration.planning_node.get_router_model",
        lambda: MagicMock(invoke=_boom),
    )
    state = {"user_input": "optimize a system", "intent": "complex"}
    out = plan_task(state)
    assert out["plan"] == []
    assert out["plan_block"] == ""


def test_plan_task_malformed_json_fails_open(monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.max_plan_steps", 5)
    resp = MagicMock(content="not json at all")
    monkeypatch.setattr(
        "jarvis.orchestration.planning_node.get_router_model",
        lambda: MagicMock(invoke=lambda p: resp),
    )
    state = {"user_input": "strategy for growth", "intent": "complex"}
    out = plan_task(state)
    assert out["plan"] == []


def test_format_plan_block_empty():
    assert format_plan_block("") == ""
    assert format_plan_block("   ") == ""


# ---------------------------------------------------------------------------
# Task duration enforcement
# ---------------------------------------------------------------------------


class _SlowGraph:
    """Graph whose invoke sleeps *seconds* then returns a final response."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds

    def invoke(self, state, config):
        import time

        time.sleep(self.seconds)
        state["final_response"] = "done"
        return state


def test_invoke_without_cap_runs_directly(monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.max_task_duration_seconds", 0)
    monkeypatch.setattr(runner, "jarvis_graph", _SlowGraph(0))
    state = {"user_input": "x"}
    out = runner._invoke_with_timeout(state, "t1")
    assert out["final_response"] == "done"


def test_invoke_within_cap_succeeds(monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.max_task_duration_seconds", 5)
    monkeypatch.setattr(runner, "jarvis_graph", _SlowGraph(0.05))
    state = {"user_input": "x"}
    out = runner._invoke_with_timeout(state, "t1")
    assert out["final_response"] == "done"


def test_invoke_exceeding_cap_raises(monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.max_task_duration_seconds", 1)
    monkeypatch.setattr(runner, "jarvis_graph", _SlowGraph(3))
    state = {"user_input": "x"}
    with pytest.raises(runner.TaskTimeoutError):
        runner._invoke_with_timeout(state, "t1")