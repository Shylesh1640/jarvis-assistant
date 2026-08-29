"""Tests for Phase 13 deep thinking mode."""
from __future__ import annotations

import pytest

from jarvis.config.settings import Settings
from jarvis.deep_thinking import generate_reasoning_chain, should_trigger_deep_thinking
from jarvis.orchestration.deep_think import deep_think
from jarvis.orchestration.state import JarvisState


class _FakeLLM:
    def __init__(self, response: str = "STEP 1:\nSub-problem: test\nAnalysis: ok\nConclusion: yes\nConfidence: 0.9\n\nFINAL SYNTHESIS:\nFinal answer\n\nTOTAL CONFIDENCE: 0.9"):
        self.response = response
        self.last_prompt = None

    def invoke(self, prompt, **kwargs):
        self.last_prompt = prompt
        from types import SimpleNamespace
        return SimpleNamespace(content=self.response)


@pytest.fixture
def patched_settings(monkeypatch):
    s = Settings()
    monkeypatch.setattr("jarvis.deep_thinking.settings", s)
    monkeypatch.setattr("jarvis.orchestration.deep_think.settings", s)
    return s


class TestDeepThinkingTrigger:
    def test_disabled_when_feature_off(self, patched_settings):
        patched_settings.deep_thinking_enabled = False
        assert should_trigger_deep_thinking("analyze this problem") is False

    def test_disabled_when_auto_trigger_off(self, patched_settings):
        patched_settings.deep_thinking_enabled = True
        patched_settings.deep_thinking_auto_trigger = False
        assert should_trigger_deep_thinking("analyze this problem") is False

    def test_triggers_on_high_confidence(self, patched_settings):
        patched_settings.deep_thinking_enabled = True
        patched_settings.deep_thinking_auto_trigger = True
        patched_settings.deep_thinking_auto_trigger_confidence_threshold = 0.5
        assert should_trigger_deep_thinking("hello", confidence=0.8) is True

    def test_triggers_on_complex_keywords(self, patched_settings):
        patched_settings.deep_thinking_enabled = True
        patched_settings.deep_thinking_auto_trigger = True
        patched_settings.deep_thinking_auto_trigger_confidence_threshold = 0.9
        assert should_trigger_deep_thinking("analyze and compare these options") is True

    def test_triggers_on_long_question(self, patched_settings):
        patched_settings.deep_thinking_enabled = True
        patched_settings.deep_thinking_auto_trigger = True
        patched_settings.deep_thinking_auto_trigger_confidence_threshold = 0.9
        long_q = " ".join(["word"] * 51)
        assert should_trigger_deep_thinking(long_q) is True

    def test_no_trigger_for_simple_question(self, patched_settings):
        patched_settings.deep_thinking_enabled = True
        patched_settings.deep_thinking_auto_trigger = True
        patched_settings.deep_thinking_auto_trigger_confidence_threshold = 0.9
        assert should_trigger_deep_thinking("hello") is False


class TestReasoningChainGeneration:
    def test_generates_chain(self, monkeypatch):
        fake = _FakeLLM()
        monkeypatch.setattr("jarvis.deep_thinking.get_model_named", lambda *a, **k: fake)
        result = generate_reasoning_chain("test question")
        assert "Final answer" in result["final_synthesis"]
        assert result["total_confidence"] == 0.9
        assert result["tokens_used"] == len(fake.response.split())
        assert result["latency_ms"] >= 0

    def test_generates_chain_with_model_name(self, monkeypatch):
        fake = _FakeLLM()
        monkeypatch.setattr("jarvis.deep_thinking.get_model_named", lambda *a, **k: fake)
        generate_reasoning_chain("test", model_name="custom-model")
        assert fake.last_prompt is not None

    def test_handles_llm_failure(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("ollama down")
        monkeypatch.setattr("jarvis.deep_thinking.get_model_named", boom)
        result = generate_reasoning_chain("test")
        assert result["steps"] == []
        assert result["error"] == "ollama down"
        assert result["latency_ms"] >= 0


class TestDeepThinkNode:
    def test_disabled_when_not_enabled(self, patched_settings):
        state: JarvisState = {
            "deep_thinking_enabled": False,
            "user_input": "hello",
        }
        out = deep_think(state)
        assert out["deep_thinking_used"] is False
        assert out["reasoning_chain"] == []

    def test_manual_trigger_via_question(self, patched_settings, monkeypatch):
        patched_settings.deep_thinking_enabled = True
        patched_settings.deep_thinking_auto_trigger = True
        patched_settings.deep_thinking_auto_trigger_confidence_threshold = 0.9
        fake = _FakeLLM()
        monkeypatch.setattr("jarvis.models.ollama_client.get_model_named", lambda *a, **k: fake)
        state: JarvisState = {
            "deep_thinking_enabled": True,
            "user_input": "think deeply about this",
            "complexity_score": 10,
            "reasoning_strategy": "cot",
        }
        out = deep_think(state)
        assert out["deep_thinking_used"] is True
        assert out["reasoning_strategy"] == "cot"
        assert out["reasoning_steps"] == 3

    def test_disabled_behavior_no_changes(self, patched_settings):
        state: JarvisState = {
            "deep_thinking_enabled": False,
            "user_input": "hello",
            "reasoning_chain": [{"step_number": 1}],
        }
        out = deep_think(state)
        assert out["reasoning_chain"] == [{"step_number": 1}]

    def test_max_steps_respected(self, patched_settings, monkeypatch):
        patched_settings.deep_thinking_enabled = True
        patched_settings.deep_thinking_max_reasoning_steps = 2
        response = "\n".join([
            "STEP 1:", "Sub-problem: a", "Analysis: a", "Conclusion: a", "Confidence: 0.5",
            "STEP 2:", "Sub-problem: b", "Analysis: b", "Conclusion: b", "Confidence: 0.5",
            "STEP 3:", "Sub-problem: c", "Analysis: c", "Conclusion: c", "Confidence: 0.5",
            "FINAL SYNTHESIS:", "final",
            "TOTAL CONFIDENCE: 0.5",
        ])
        fake = _FakeLLM(response)
        monkeypatch.setattr("jarvis.models.ollama_client.get_model_named", lambda *a, **k: fake)
        state: JarvisState = {
            "deep_thinking_enabled": True,
            "user_input": "think deeply about a complex problem",
            "complexity_score": 10,
            "reasoning_strategy": "cot",
        }
        out = deep_think(state)
        assert out["reasoning_steps"] == 2


class TestConfigurationOptions:
    def test_runtime_settings_update(self, patched_settings):
        patched_settings.deep_thinking_enabled = True
        patched_settings.deep_thinking_auto_trigger = True
        patched_settings.deep_thinking_max_reasoning_steps = 5
        patched_settings.deep_thinking_max_tokens_factor = 3.0
        patched_settings.deep_thinking_show_reasoning_chain = False
        assert patched_settings.deep_thinking_enabled is True
        assert patched_settings.deep_thinking_auto_trigger is True
        assert patched_settings.deep_thinking_max_reasoning_steps == 5
        assert patched_settings.deep_thinking_max_tokens_factor == 3.0
        assert patched_settings.deep_thinking_show_reasoning_chain is False
