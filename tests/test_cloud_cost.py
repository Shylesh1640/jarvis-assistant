"""Tests for Phase 6 cloud cost tracking, budgets and approval."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jarvis.models import cloud_pricing as cp
from jarvis.models import openrouter_client as orc
from jarvis.models.cost_guard import (
    CloudRequestCostExceededError,
    CloudSessionBudgetExceededError,
    CostGuard,
    reload_cost_guard,
)
from jarvis.orchestration import branches as branches_mod
from jarvis.orchestration.branches import run_complex_branch
from jarvis.persistence import create_all
from jarvis.persistence import repos
from jarvis.persistence.engine import reset_engine_for_tests

_MSGS = [
    {"role": "system", "content": "You are a helpful assistant." * 20},
    {"role": "user", "content": "Explain distributed systems in depth." * 40},
]


@pytest.fixture(autouse=True)
def _fresh_guard(monkeypatch):
    reset_engine_for_tests()
    create_all()
    reload_cost_guard()
    cp.reload_pricing()


# ---------------------------------------------------------------------------
# pricing config
# ---------------------------------------------------------------------------


def test_price_exact_model_match():
    price = cp.price_for("anthropic/claude-opus-4.1")
    assert price["prompt_per_1m_usd"] == 15.0


def test_price_substring_rule():
    price = cp.price_for("anthropic/claude-sonnet-4.5")
    assert price["prompt_per_1m_usd"] == 15.0


def test_price_default_for_unknown():
    price = cp.price_for("completely/unknown-model")
    assert price["prompt_per_1m_usd"] > 0


def test_price_longest_rule_wins():
    price = cp.price_for("some/deepseek-model")
    assert price["prompt_per_1m_usd"] == 0.55


def test_load_falls_back_to_defaults_when_file_missing(monkeypatch, tmp_path):
    cp.reload_pricing()
    monkeypatch.setattr(
        "jarvis.config.settings.settings.cloud_pricing_config_path",
        str(tmp_path / "missing.json"),
    )
    table = cp.load_pricing()
    assert "anthropic/claude-opus-4.1" in table["models"]


def test_malformed_pricing_file_ignored(monkeypatch, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    cp.reload_pricing()
    monkeypatch.setattr(
        "jarvis.config.settings.settings.cloud_pricing_config_path", str(bad)
    )
    assert cp.load_pricing()["default_prompt_per_1m_usd"] > 0


def test_estimate_cost_uses_prompt_and_completion():
    cost = cp.estimate_cost_usd("openai/gpt-5.5", 1_000_000, 1_000_000)
    assert cost == pytest.approx(1.25 + 10.0)


# ---------------------------------------------------------------------------
# CostGuard budgets
# ---------------------------------------------------------------------------


def test_request_cost_cap_raises(monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.cloud_max_request_cost_usd", 0.01)
    g = CostGuard(max_prompt_tokens=0, daily_budget_usd=0.0)
    with pytest.raises(CloudRequestCostExceededError):
        g.check_request_cost("openai/gpt-5.5", 0.5)


def test_request_cost_cap_zero_disabled(monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.cloud_max_request_cost_usd", 0.0)
    g = CostGuard(max_prompt_tokens=0, daily_budget_usd=0.0)
    g.check_request_cost("openai/gpt-5.5", 99.0)  # no raise


def test_session_cost_cap_raises(monkeypatch):
    reset_engine_for_tests()
    create_all()
    monkeypatch.setattr("jarvis.config.settings.settings.cloud_max_session_cost_usd", 0.05)
    g = CostGuard(max_prompt_tokens=0, daily_budget_usd=0.0)
    g.record_call("openai/gpt-5.5", _MSGS, session_id="sess-1", usage={"prompt_tokens": 40_000, "completion_tokens": 5_000})
    with pytest.raises(CloudSessionBudgetExceededError):
        g.check_session_cost("sess-1", 0.04)


def test_session_cost_cap_ignores_other_sessions(monkeypatch):
    reset_engine_for_tests()
    create_all()
    monkeypatch.setattr("jarvis.config.settings.settings.cloud_max_session_cost_usd", 0.05)
    g = CostGuard(max_prompt_tokens=0, daily_budget_usd=0.0)
    g.record_call("openai/gpt-5.5", _MSGS, session_id="sess-a", usage={"prompt_tokens": 40_000, "completion_tokens": 5_000})
    g.check_session_cost("sess-b", 0.04)  # different session -> allowed


def test_record_call_persists_to_db(monkeypatch):
    reset_engine_for_tests()
    create_all()
    monkeypatch.setattr("jarvis.config.settings.settings.cloud_cost_tracking_enabled", True)
    g = CostGuard(max_prompt_tokens=0, daily_budget_usd=0.0)
    g.record_call("openai/gpt-5.5", _MSGS, session_id="persist-sess", usage={"prompt_tokens": 1000, "completion_tokens": 100})
    rows = repos.cloud_usage.recent()
    assert len(rows) == 1
    assert rows[0]["model"] == "openai/gpt-5.5"
    assert rows[0]["session_id"] == "persist-sess"
    assert rows[0]["estimated_cost_usd"] > 0
    assert repos.cloud_usage.sum_for_session("persist-sess") > 0


def test_stats_include_phase6_fields():
    g = CostGuard(max_prompt_tokens=0, daily_budget_usd=0.0)
    stats = g.stats()
    assert "request_cost_cap_usd" in stats
    assert "session_cost_cap_usd" in stats
    assert "cost_tracking_enabled" in stats
    assert "require_cost_approval" in stats


# ---------------------------------------------------------------------------
# OpenRouter integration
# ---------------------------------------------------------------------------


def test_post_chat_returns_usage(monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.openrouter_api_key", "sk-test")
    payload = {
        "choices": [{"message": {"content": "hello"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    monkeypatch.setattr(orc.httpx, "post", MagicMock(return_value=_FakeResp(payload)))
    text, usage = orc._post_chat("openai/gpt-5.5", _MSGS, 0.4)
    assert text == "hello"
    assert usage == {"prompt_tokens": 10, "completion_tokens": 5}


def test_post_chat_usage_defaults_zero(monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.openrouter_api_key", "sk-test")
    payload = {"choices": [{"message": {"content": "hi"}}]}
    monkeypatch.setattr(orc.httpx, "post", MagicMock(return_value=_FakeResp(payload)))
    _text, usage = orc._post_chat("openai/gpt-5.5", _MSGS, 0.4)
    assert usage == {"prompt_tokens": 0, "completion_tokens": 0}


def test_run_complex_records_session_usage(monkeypatch):
    reset_engine_for_tests()
    create_all()
    monkeypatch.setattr("jarvis.config.settings.settings.openrouter_api_key", "sk-test")
    monkeypatch.setattr(
        "jarvis.config.settings.settings.complex_model_chain",
        "openai/gpt-5.5",
    )
    monkeypatch.setattr(
        "jarvis.config.settings.settings.cloud_cost_tracking_enabled", True
    )
    g = CostGuard(max_prompt_tokens=0, daily_budget_usd=0.0)
    monkeypatch.setattr(orc, "get_cost_guard", lambda: g)
    monkeypatch.setattr(
        orc, "_post_chat",
        MagicMock(return_value=("answer", {"prompt_tokens": 500, "completion_tokens": 50})),
    )
    text, model = orc.run_complex_with_fallback(_MSGS, session_id="cloud-sess")
    assert text == "answer"
    assert model == "openai/gpt-5.5"
    rows = repos.cloud_usage.recent()
    assert rows and rows[0]["session_id"] == "cloud-sess"


def test_run_complex_refuses_overpriced_request(monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.openrouter_api_key", "sk-test")
    monkeypatch.setattr(
        "jarvis.config.settings.settings.complex_model_chain",
        "openai/gpt-5.5",
    )
    monkeypatch.setattr("jarvis.config.settings.settings.cloud_max_request_cost_usd", 0.01)
    g = CostGuard(max_prompt_tokens=0, daily_budget_usd=0.0)
    monkeypatch.setattr(orc, "get_cost_guard", lambda: g)
    monkeypatch.setattr(orc, "_post_chat", MagicMock())
    huge = [{"role": "user", "content": "word " * 50_000}]
    with pytest.raises(CloudRequestCostExceededError):
        orc.run_complex_with_fallback(huge)
    orc._post_chat.assert_not_called()


class _FakeResp:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


# ---------------------------------------------------------------------------
# cloud-cost approval gate in the complex branch
# ---------------------------------------------------------------------------


def _state(user_input: str, **extra) -> dict:
    base = {"user_input": user_input, "history": [], "fallback_count": 0}
    base.update(extra)
    return base


def _pin_cloud_settings(monkeypatch):
    for name in ("openrouter_api_key",):
        monkeypatch.setattr(branches_mod.settings, name, "sk-test", raising=True)
    monkeypatch.setattr(
        branches_mod.settings, "complex_model_chain",
        "anthropic/claude-opus-4.1", raising=True,
    )
    monkeypatch.setattr(
        branches_mod.settings, "cloud_require_cost_approval", True, raising=True
    )
    monkeypatch.setattr(
        branches_mod.settings, "cloud_cost_tracking_enabled", True, raising=True
    )
    monkeypatch.setattr(
        branches_mod.settings, "cloud_max_prompt_tokens", 0, raising=True
    )
    monkeypatch.setattr(
        branches_mod.settings, "cloud_daily_budget_usd", 0.0, raising=True
    )


def test_complex_branch_pauses_for_cost_approval(monkeypatch):
    _pin_cloud_settings(monkeypatch)
    monkeypatch.setattr(
        branches_mod, "run_complex_with_fallback", MagicMock()
    )
    state = _state(
        "design a large distributed system", intent="complex", complexity="difficult"
    )
    run_complex_branch(state)
    assert state["approval_required"] is True
    assert "cloud_call" in state["pending_action"]
    assert state["approval_id"]
    assert state["approval_expires_at"]
    branches_mod.run_complex_with_fallback.assert_not_called()


def test_complex_branch_proceeds_when_approved(monkeypatch):
    _pin_cloud_settings(monkeypatch)

    def _fake_run(messages, session_id=None):
        return ("[cloud response]", "anthropic/claude-opus-4.1")

    monkeypatch.setattr(branches_mod, "run_complex_with_fallback", _fake_run)
    state = _state(
        "design a large distributed system",
        intent="complex", complexity="difficult", approved=True,
    )
    run_complex_branch(state)
    assert state.get("approval_required") is not True
    assert state["final_response"] == "[cloud response]"
    assert state["selected_path"] == "complex"


def test_complex_branch_no_gate_when_approval_disabled(monkeypatch):
    _pin_cloud_settings(monkeypatch)
    monkeypatch.setattr(
        branches_mod.settings, "cloud_require_cost_approval", False, raising=True
    )

    def _fake_run(messages, session_id=None):
        return ("[cloud response]", "anthropic/claude-opus-4.1")

    monkeypatch.setattr(branches_mod, "run_complex_with_fallback", _fake_run)
    state = _state(
        "design a large distributed system", intent="complex", complexity="difficult"
    )
    run_complex_branch(state)
    assert state.get("approval_required") is not True
    assert state["final_response"] == "[cloud response]"