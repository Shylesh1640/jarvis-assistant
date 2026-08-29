"""Tests for Phase 13 reasoning strategy variations."""
from __future__ import annotations

import pytest

from jarvis.config.settings import Settings
from jarvis.reasoning import (
    CoTReasoning,
    FastAndSlowReasoning,
    ReflexionReasoning,
    SelfConsistencyReasoning,
    ToTReasoning,
    reasoning_registry,
)


class _FakeLLM:
    def __init__(self, response: str = "reasoned answer"):
        self.response = response
        self.last_prompt = None
        self.invocations = 0

    def invoke(self, prompt, **kwargs):
        self.last_prompt = prompt
        self.invocations += 1
        from types import SimpleNamespace
        return SimpleNamespace(content=self.response)


@pytest.fixture
def patched_settings(monkeypatch):
    s = Settings()
    monkeypatch.setattr("jarvis.reasoning.settings", s)
    return s


class TestChainOfThought:
    def test_returns_result(self, patched_settings):
        fake = _FakeLLM()
        result = CoTReasoning().reason("test question", llm=fake)
        assert result.strategy.value == "cot"
        assert result.answer == "reasoned answer"
        assert result.latency_ms >= 0
        assert fake.invocations == 1

    def test_prompt_contains_question(self, patched_settings):
        fake = _FakeLLM()
        CoTReasoning().reason("what is 2+2", llm=fake)
        assert "what is 2+2" in str(fake.last_prompt)


class TestTreeOfThought:
    def test_returns_result(self, patched_settings):
        fake = _FakeLLM()
        result = ToTReasoning().reason("open problem", llm=fake)
        assert result.strategy.value == "tot"
        assert result.metadata.get("branches_explored", 0) >= 1

    def test_respects_max_branches(self, patched_settings):
        patched_settings.reasoning_strategy_tot_max_branches = 2
        fake = _FakeLLM()
        ToTReasoning().reason("open problem", llm=fake)
        assert "2" in str(fake.last_prompt)


class TestSelfConsistency:
    def test_returns_result(self, patched_settings):
        fake = _FakeLLM()
        result = SelfConsistencyReasoning().reason("factual question", llm=fake)
        assert result.strategy.value == "self_consistency"
        assert result.metadata.get("samples") == patched_settings.reasoning_strategy_self_consistency_num_samples


class TestReflexion:
    def test_returns_result(self, patched_settings):
        fake = _FakeLLM()
        result = ReflexionReasoning().reason("complex task", llm=fake)
        assert result.strategy.value == "reflexion"
        assert result.metadata.get("max_iterations") == patched_settings.reasoning_strategy_reflexion_max_iterations


class TestFastAndSlow:
    def test_fast_path_simple_question(self, patched_settings):
        fake = _FakeLLM()
        result = FastAndSlowReasoning().reason("hi", llm=fake)
        assert result.strategy.value == "fast_and_slow"
        assert result.metadata.get("path") == "fast"

    def test_slow_path_complex_question(self, patched_settings):
        fake = _FakeLLM()
        result = FastAndSlowReasoning().reason("analyze the architecture", llm=fake)
        assert result.metadata.get("path") == "slow"


class TestReasoningStrategyRegistry:
    def test_get_enabled(self, patched_settings):
        patched_settings.reasoning_strategy_cot_enabled = True
        patched_settings.reasoning_strategy_tot_enabled = False
        enabled = reasoning_registry.get_enabled()
        assert any(s.strategy.value == "cot" for s in enabled)
        assert not any(s.strategy.value == "tot" for s in enabled)

    def test_select_auto_complex(self, patched_settings):
        patched_settings.reasoning_strategy_self_consistency_enabled = True
        strategy = reasoning_registry.select_auto("analyze and design a system")
        assert strategy.strategy.value == "self_consistency"

    def test_select_auto_simple(self, patched_settings):
        patched_settings.reasoning_strategy_self_consistency_enabled = False
        strategy = reasoning_registry.select_auto("hello")
        assert strategy.strategy.value == "fast_and_slow"

    def test_execute_returns_result(self, patched_settings):
        fake = _FakeLLM()
        result = reasoning_registry.execute("cot", "test", llm=fake)
        assert result is not None
        assert result.strategy.value == "cot"

    def test_execute_unknown_strategy_returns_none(self, patched_settings):
        result = reasoning_registry.execute("unknown", "test")
        assert result is None
