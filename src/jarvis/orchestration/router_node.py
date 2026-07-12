"""Rule-first intent classification node."""
from jarvis.orchestration.state import JarvisState

CODING_KEYWORDS = [
    "code", "bug", "error", "function", "class", "repo", "git",
    "terminal", "traceback", "compile", "refactor", "test", "script",
]

COMPLEX_KEYWORDS = [
    "architecture", "design a system", "deep analysis", "compare in depth",
    "strategy", "long term plan", "research",
]


def classify_intent(state: JarvisState) -> JarvisState:
    text = state.get("user_input", "").lower()

    if any(word in text for word in CODING_KEYWORDS):
        state["intent"] = "coding"
    elif any(phrase in text for phrase in COMPLEX_KEYWORDS):
        state["intent"] = "complex"
    else:
        state["intent"] = "general"

    return state
