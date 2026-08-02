"""Intent + complexity classification node.

Phase 4 :: Smarter routing + model choice.

Strategy (rules-first, LLM-ready):
  1. Word-count length heuristic  -> base complexity (easy/medium/difficult).
  2. Keyword boosts                -> bump complexity / set intent.
  3. (Future) Router LLM for borderline prompts -> JSON override.

The router stores `intent`, `complexity`, `complexity_score` in state so
that `select_model()` downstream can pick the right model for the job.
"""
import logging

from jarvis.orchestration.state import JarvisState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword dictionaries
# ---------------------------------------------------------------------------

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

# Phrases that bump complexity up by one notch (don't force `complex` intent,
# just signal "this is harder than the word count suggests").
COMPLEXITY_BOOST_PHRASES = [
    "architecture", "design a system", "optimize", "multi-objective",
    "strategy", "long term plan", "research", "traffic light",
    "priority system", "minimize waiting time", "ensure pedestrian safety",
    "deep analysis", "compare in depth", "adapts in real-time",
]


# ---------------------------------------------------------------------------
# Complexity (length + boost)
# ---------------------------------------------------------------------------

def _length_complexity(n_words: int) -> str:
    if n_words > 80:
        return "difficult"
    if n_words > 30:
        return "medium"
    return "easy"


def _bump_once(level: str) -> str:
    order = ("easy", "medium", "difficult")
    idx = order.index(level) if level in order else 0
    return order[min(idx + 1, len(order) - 1)]


def _classify_complexity(text: str) -> tuple[str, int]:
    """Return (complexity, raw_word_count)."""
    words = text.split()
    n = len(words)
    complexity = _length_complexity(n)
    text_lower = text.lower()
    if any(p in text_lower for p in COMPLEXITY_BOOST_PHRASES):
        complexity = _bump_once(complexity)
    return complexity, n


# ---------------------------------------------------------------------------
# Future hook: LLM router for borderline prompts
# ---------------------------------------------------------------------------

def _router_llm_classify(text: str) -> dict | None:
    """Borderline-prompt LLM classifier.

    Not wired yet — returns None so the rules path always wins. Phase 5+
    can implement this against a small local model (e.g. qwen3:8b in JSON
    mode) returning `{"intent": ..., "complexity": ...}`. Returning None
    here means "no opinion, defer to the rules classifier".
    """
    return None


# ---------------------------------------------------------------------------
# Top-level node
# ---------------------------------------------------------------------------

def classify_intent(state: JarvisState) -> JarvisState:
    text = state.get("user_input", "")
    text_lower = text.lower()

    complexity, n_words = _classify_complexity(text)
    state["complexity"] = complexity
    state["complexity_score"] = n_words

    # Future hook: when an LLM router lands and decides to override, it can
    # mutate both intent and complexity here. Keeping the call site explicit
    # makes the eventual wiring a one-line change.
    llm_override = _router_llm_classify(text)
    if llm_override is not None:
        state["intent"] = llm_override.get("intent", "general")
        state["complexity"] = llm_override.get("complexity", complexity)
        logger.info("Router LLM override applied")
        return _log_and_return(state)

    # Coding short-circuit (preserves existing behavior: a coding keyword
    # wins even if a complex phrase is also present).
    if any(word in text_lower for word in CODING_KEYWORDS):
        state["intent"] = "coding"
        return _log_and_return(state)

    if any(phrase in text_lower for phrase in COMPLEX_KEYWORDS):
        state["intent"] = "complex"
        # The complex keyword itself signals moderate difficulty at least.
        if complexity == "easy":
            state["complexity"] = "medium"
    elif complexity == "difficult":
        state["intent"] = "complex"
    else:
        state["intent"] = "general"

    return _log_and_return(state)


def _log_and_return(state: JarvisState) -> JarvisState:
    logger.info(
        "Intent classified: %s (complexity=%s, words=%s)",
        state.get("intent"),
        state.get("complexity"),
        state.get("complexity_score"),
    )
    return state
