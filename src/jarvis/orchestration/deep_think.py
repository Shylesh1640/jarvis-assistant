"""Deep thinking node for the LangGraph orchestration.

Runs after context retrieval and before branch selection. When deep thinking
is enabled (and either auto-triggered or manually requested), it executes the
configured reasoning strategy and stores the result in state.
"""
from __future__ import annotations

import logging

from jarvis.config.settings import settings
from jarvis.deep_thinking import should_trigger_deep_thinking
from jarvis.orchestration.state import JarvisState
from jarvis.reasoning import reasoning_registry

logger = logging.getLogger(__name__)


def _select_strategy(state: JarvisState, question: str) -> str:
    override = state.get("reasoning_strategy")
    if override and override != "auto":
        return override
    return settings.reasoning_strategy_default


def deep_think(state: JarvisState) -> JarvisState:
    """Execute deep thinking with the selected reasoning strategy."""
    state.setdefault("deep_thinking_used", False)
    state.setdefault("reasoning_chain", [])
    state.setdefault("reasoning_sub_problems", [])
    state.setdefault("reasoning_confidence", 0.0)
    state.setdefault("reasoning_steps", 0)
    state.setdefault("tokens_used_reasoning", 0)
    state.setdefault("latency_ms_reasoning", 0)
    state.setdefault("reasoning_strategy", None)

    if not state.get("deep_thinking_enabled"):
        return state

    question = state.get("user_input", "")
    manual_override = (
        "think deeply" in question.lower()
        or "step by step" in question.lower()
        or state.get("deep_thinking_show_reasoning", False)
    )

    confidence = state.get("complexity_score", 0) / 100.0
    should_trigger = manual_override or should_trigger_deep_thinking(question, confidence)

    if not should_trigger:
        return state

    strategy_name = _select_strategy(state, question)
    impl = None
    try:
        if strategy_name == "auto":
            impl = reasoning_registry.select_auto(question)
        else:
            impl = reasoning_registry.get(strategy_name)
    except Exception as exc:
        logger.warning("Reasoning strategy selection failed: %s", exc)

    if impl is None:
        state["warning"] = "Deep thinking strategy unavailable."
        return state

    try:
        result = impl.reason(question, state=state)
    except Exception as exc:
        logger.warning("Reasoning strategy execution failed: %s", exc)
        state["warning"] = f"Deep thinking failed: {exc}"
        return state

    max_steps = max(1, settings.deep_thinking_max_reasoning_steps)

    steps = result.metadata.get("steps", [])
    if isinstance(steps, int):
        steps = [
            {
                "step_number": i + 1,
                "description": "reasoning",
                "sub_problem": question,
                "analysis": result.reasoning,
                "conclusion": result.answer,
                "confidence": result.confidence,
                "citations": [],
            }
            for i in range(min(steps, max_steps))
        ]
    if not steps and result.reasoning:
        steps = [{
            "step_number": 1,
            "description": "reasoning",
            "sub_problem": question,
            "analysis": result.reasoning,
            "conclusion": result.answer,
            "confidence": result.confidence,
            "citations": [],
        }]

    if isinstance(steps, list):
        steps = steps[:max_steps]

    state["reasoning_chain"] = steps
    state["reasoning_sub_problems"] = result.metadata.get("sub_problems", [])
    state["reasoning_confidence"] = result.confidence
    state["reasoning_steps"] = len(steps)
    state["tokens_used_reasoning"] = result.tokens_used
    state["latency_ms_reasoning"] = result.latency_ms
    state["reasoning_strategy"] = result.strategy.value
    state["deep_thinking_used"] = True

    logger.info(
        "Deep thinking [%s] generated %d steps (confidence=%.2f, latency=%dms)",
        result.strategy.value,
        len(steps),
        result.confidence,
        result.latency_ms,
    )
    return state


__all__ = ["deep_think"]
