"""Tests for Phase 7 cost guardrails (CostGuard + OpenRouter integration)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from jarvis.api import routes
from jarvis.api.main import app
from jarvis.models import openrouter_client as orc
from jarvis.models.cost_guard import (
    CloudBudgetExceededError,
    CloudPromptTooLargeError,
    CostGuard,
    estimate_prompt_cost_usd,
    reload_cost_guard,
)

_MSGS = [
    {"role": "system", "content": "You are a helpful assistant." * 20},
    {"role": "user", "content": "Explain distributed systems in depth." * 40},
]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.cloud_max_prompt_tokens", 0)
    monkeypatch.setattr("jarvis.config.settings.settings.cloud_daily_budget_usd", 0.0)
    reload_cost_guard()
    routes.chat._sessions.clear()
    routes.chat._pending_approvals.clear()
    return TestClient(app)


def test_guard_disabled_by_default():
    reload_cost_guard()
    g = CostGuard(max_prompt_tokens=0, daily_budget_usd=0.0)
    g.check_prompt(_MSGS, "some/model")
    g.check_budget()
    assert g.stats()["daily_budget_usd"] == 0.0


def test_prompt_too_large_raises():
    g = CostGuard(max_prompt_tokens=5, daily_budget_usd=0.0)
    with pytest.raises(CloudPromptTooLargeError):
        g.check_prompt(_MSGS, "some/model")


def test_small_prompt_passes():
    g = CostGuard(max_prompt_tokens=10_000_000, daily_budget_usd=0.0)
    g.check_prompt([{"role": "user", "content": "hi"}], "some/model")


def test_budget_exceeded_raises():
    g = CostGuard(max_prompt_tokens=0, daily_budget_usd=0.0001)
    g.check_budget()  # 0 spent so far -> allowed
    g.record_call("anthropic/claude-opus-4.1", _MSGS)
    with pytest.raises(CloudBudgetExceededError):
        g.check_budget()


def test_budget_reset_next_day():
    g = CostGuard(max_prompt_tokens=0, daily_budget_usd=0.001)
    g.record_call("anthropic/claude-opus-4.1", _MSGS)
    # Simulate a new UTC day.
    with patch("jarvis.models.cost_guard.datetime") as mock_dt:
        mock_dt.now.return_value.strftime.return_value = "2026-01-02"
        g.check_budget()  # rollover resets spend -> allowed


def test_estimate_cost_is_positive():
    cost = estimate_prompt_cost_usd("anthropic/claude-opus-4.1", _MSGS)
    assert cost > 0


def test_record_call_tracks_stats():
    g = CostGuard(max_prompt_tokens=0, daily_budget_usd=0.0)
    g.record_call("openai/gpt-5.5", _MSGS)
    stats = g.stats()
    assert stats["calls_today"] == 1
    assert stats["spent_today_usd"] > 0
    assert stats["recent_calls"][0]["model"] == "openai/gpt-5.5"


def test_openrouter_refuses_when_budget_tripped(monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.openrouter_api_key", "sk-test")
    monkeypatch.setattr(
        "jarvis.config.settings.settings.complex_model_chain",
        "anthropic/claude-opus-4.1",
    )
    g = CostGuard(max_prompt_tokens=0, daily_budget_usd=0.01)
    g._spent_today = 1.0  # force over-budget with a fresh guard
    monkeypatch.setattr(orc, "get_cost_guard", lambda: g)
    monkeypatch.setattr(orc, "_post_chat", MagicMock(return_value=("text", {"prompt_tokens": 100, "completion_tokens": 10})))
    with pytest.raises(CloudBudgetExceededError):
        orc.run_complex_with_fallback(_MSGS)
    orc._post_chat.assert_not_called()


def test_openrouter_refuses_oversized_prompt(monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.openrouter_api_key", "sk-test")
    monkeypatch.setattr(
        "jarvis.config.settings.settings.complex_model_chain",
        "openai/gpt-5.5",
    )
    g = CostGuard(max_prompt_tokens=5, daily_budget_usd=0.0)
    monkeypatch.setattr(orc, "get_cost_guard", lambda: g)
    monkeypatch.setattr(orc, "_post_chat", MagicMock(return_value=("text", {"prompt_tokens": 100, "completion_tokens": 10})))
    with pytest.raises(CloudPromptTooLargeError):
        orc.run_complex_with_fallback(_MSGS)
    orc._post_chat.assert_not_called()


def test_openrouter_success_records_cost(monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.openrouter_api_key", "sk-test")
    monkeypatch.setattr(
        "jarvis.config.settings.settings.complex_model_chain",
        "openai/gpt-5.5",
    )
    g = CostGuard(max_prompt_tokens=0, daily_budget_usd=0.0)
    monkeypatch.setattr(orc, "get_cost_guard", lambda: g)
    monkeypatch.setattr(orc, "_post_chat", MagicMock(return_value=("cloud answer", {"prompt_tokens": 100, "completion_tokens": 10})))
    text, model = orc.run_complex_with_fallback(_MSGS)
    assert text == "cloud answer"
    assert model == "openai/gpt-5.5"
    assert g.stats()["calls_today"] == 1


def test_cost_route(client):
    r = client.get("/cost")
    assert r.status_code == 200
    body = r.json()
    assert "spent_today_usd" in body
    assert "daily_budget_usd" in body
    assert "calls_today" in body
    assert "max_prompt_tokens" in body