"""Rule-first intent classification node with length-based complexity."""
import logging

from jarvis.orchestration.state import JarvisState

logger = logging.getLogger(__name__)

CODING_KEYWORDS = [
    "code", "bug", "error", "function", "class", "repo", "git",
    "terminal", "traceback", "compile", "refactor", "test", "script",
]

COMPLEX_KEYWORDS = [
    "architecture", "design a system", "deep analysis", "compare in depth",
    "strategy", "long term plan", "research", "optimize", "multi-objective",
    "system design", "traffic light", "busy intersection",
    "minimize waiting", "emergency vehicle", "pedestrian safety",
    "adapts in real-time",
]


def _classify_complexity(text: str) -> str:
    words = text.split()
    n = len(words)
    if n > 80:
        return "difficult"
    if n > 30:
        return "medium"
    return "easy"


def classify_intent(state: JarvisState) -> JarvisState:
    text = state.get("user_input", "").lower()

    if any(word in text for word in CODING_KEYWORDS):
        state["intent"] = "coding"
        state["complexity"] = _classify_complexity(text)
        logger.info(
            "Intent classified: %s (complexity=%s)",
            state["intent"],
            state["complexity"],
        )
        return state

    complexity = _classify_complexity(text)
    state["complexity"] = complexity

    if any(phrase in text for phrase in COMPLEX_KEYWORDS):
        state["intent"] = "complex"
        if complexity == "easy":
            state["complexity"] = "medium"
    elif complexity == "difficult":
        state["intent"] = "complex"
    else:
        state["intent"] = "general"

    logger.info(
        "Intent classified: %s (complexity=%s)",
        state["intent"],
        state["complexity"],
    )
    return state
