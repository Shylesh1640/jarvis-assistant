from types import SimpleNamespace

import jarvis.orchestration.router_node as rn
from jarvis.orchestration.router_node import classify_intent


# ---------------------------------------------------------------------------
# Router LLM: borderline prompts get a JSON override from the router model
# ---------------------------------------------------------------------------

_BORDERLINE_PROMPT = (
    "Can you tell me what the capital of France is "
    "and also what the capital of Germany is "
    "and also what the capital of Italy is "
    "and also what the capital of Spain is?"
)


class _StubRouterLLM:
    """Stand-in for get_router_model(): records prompt, returns scripted content."""

    def __init__(self, content: str):
        self.content = content
        self.last_prompt: str | None = None

    def invoke(self, prompt, **kwargs):
        self.last_prompt = prompt
        return SimpleNamespace(content=self.content, tool_calls=[])


def _patch_router(monkeypatch, content: str) -> _StubRouterLLM:
    stub = _StubRouterLLM(content)
    monkeypatch.setattr(rn, "get_router_model", lambda: stub)
    return stub


def test_router_llm_override_applies(monkeypatch):
    stub = _patch_router(monkeypatch, '{"intent": "complex", "complexity": "difficult"}')
    state = {"user_input": _BORDERLINE_PROMPT}
    result = classify_intent(state)
    assert stub.last_prompt is not None
    assert "classifier" in stub.last_prompt
    assert result["intent"] == "complex"
    assert result["complexity"] == "difficult"


def test_router_llm_borderline_medium_prompt_calls_model(monkeypatch):
    stub = _patch_router(monkeypatch, '{"intent": "general", "complexity": "medium"}')
    state = {"user_input": _BORDERLINE_PROMPT}
    classify_intent(state)
    assert stub.last_prompt is not None


def test_router_llm_skipped_for_keyword_prompts(monkeypatch):
    stub = _patch_router(monkeypatch, '{"intent": "complex", "complexity": "difficult"}')
    state = {"user_input": "fix this bug in my python function"}
    result = classify_intent(state)
    assert stub.last_prompt is None
    assert result["intent"] == "coding"


def test_router_llm_skipped_for_short_prompts(monkeypatch):
    stub = _patch_router(monkeypatch, '{"intent": "complex", "complexity": "difficult"}')
    state = {"user_input": "hi there"}
    result = classify_intent(state)
    assert stub.last_prompt is None
    assert result["intent"] == "general"
    assert result["complexity"] == "easy"


def test_router_llm_malformed_json_falls_back_to_rules(monkeypatch):
    stub = _patch_router(monkeypatch, "not json at all")
    state = {"user_input": _BORDERLINE_PROMPT}
    result = classify_intent(state)
    assert stub.last_prompt is not None
    assert result["intent"] == "general"
    assert result["complexity"] == "medium"


def test_router_llm_invalid_values_fall_back_to_rules(monkeypatch):
    stub = _patch_router(monkeypatch, '{"intent": "banana", "complexity": "meh"}')
    state = {"user_input": _BORDERLINE_PROMPT}
    result = classify_intent(state)
    assert stub.last_prompt is not None
    assert result["intent"] == "general"


def test_router_llm_exception_falls_back_to_rules(monkeypatch):
    def boom():
        raise RuntimeError("ollama down")

    monkeypatch.setattr(rn, "get_router_model", boom)
    state = {"user_input": _BORDERLINE_PROMPT}
    result = classify_intent(state)
    assert result["intent"] == "general"
    assert result["complexity"] == "medium"


def test_router_llm_disabled_returns_rules(monkeypatch):
    from jarvis.config.settings import settings

    monkeypatch.setattr(settings, "router_llm_enabled", False)
    stub = _patch_router(monkeypatch, '{"intent": "complex", "complexity": "difficult"}')
    state = {"user_input": _BORDERLINE_PROMPT}
    result = classify_intent(state)
    assert stub.last_prompt is None
    assert result["intent"] == "general"


def test_general_intent():
    state = {"user_input": "what is the weather like today"}
    result = classify_intent(state)
    assert result["intent"] == "general"
    assert result["complexity"] == "easy"


def test_coding_intent():
    state = {"user_input": "fix this bug in my python function"}
    result = classify_intent(state)
    assert result["intent"] == "coding"
    assert result["complexity"] == "easy"


def test_complex_intent():
    state = {"user_input": "design a system architecture for this project"}
    result = classify_intent(state)
    assert result["intent"] == "complex"
    assert result["complexity"] == "medium"


def test_traffic_light_prompt_classified_complex():
    prompt = (
        "Design an AI-powered traffic light system for a busy city intersection. "
        "The system should minimize average waiting time for all vehicles, "
        "prioritize emergency vehicles when they approach the intersection, "
        "ensure pedestrian safety with dedicated crossing phases, "
        "and adapt in real-time to changing traffic patterns. "
        "Provide a detailed architecture and optimization strategy."
    )
    state = {"user_input": prompt}
    result = classify_intent(state)
    assert result["intent"] == "complex"
    assert result["complexity"] in ("medium", "difficult")


def test_long_prompt_without_keywords_classified_complex():
    prompt = (
        "I need you to think about all the things that could possibly go wrong "
        "in a very detailed scenario that spans multiple domains and requires "
        "careful step by step analysis of each component and subsystem involved "
        "in the process from start to finish including all edge cases and "
        "potential failure modes that might arise during normal operation or "
        "under exceptional circumstances that would require special handling "
        "and additional consideration for each of the many complex factors "
        "that we have not yet fully accounted for in our initial assessment "
        "of the situation at hand which is becoming increasingly complicated "
        "with every passing moment that we spend thinking about this problem."
    )
    state = {"user_input": prompt}
    result = classify_intent(state)
    assert result["complexity"] == "difficult"
    assert result["intent"] == "complex"


def test_medium_length_general_prompt():
    prompt = (
        "Can you tell me what the capital of France is "
        "and also what the capital of Germany is "
        "and also what the capital of Italy is "
        "and also what the capital of Spain is?"
    )
    state = {"user_input": prompt}
    result = classify_intent(state)
    assert result["intent"] == "general"
    assert result["complexity"] == "medium"


# ---------------------------------------------------------------------------
# Phase 4 extras: complexity_score field and complexity boost
# ---------------------------------------------------------------------------

def test_complexity_score_is_word_count():
    state = {"user_input": "one two three four"}
    result = classify_intent(state)
    assert result["complexity_score"] == 4
    assert result["complexity"] == "easy"


def test_architecture_keyword_boosts_easy_to_medium():
    # 5 words would normally be "easy", but "architecture" raises it.
    state = {"user_input": "explain the architecture in depth"}
    result = classify_intent(state)
    assert result["complexity"] == "medium"
    assert result["intent"] == "complex"  # architecture is also a complex keyword


def test_router_does_not_touch_existing_easy_general_classification():
    state = {"user_input": "hi there"}
    result = classify_intent(state)
    assert result["intent"] == "general"
    assert result["complexity"] == "easy"
    assert result["complexity_score"] == 2
