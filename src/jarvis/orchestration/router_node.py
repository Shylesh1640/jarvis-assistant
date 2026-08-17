"""Intent + complexity classification node.

Phase 4 :: Smarter routing + model choice.

Strategy (rules-first, LLM-ready):
  1. Word-count length heuristic  -> base complexity (easy/medium/difficult).
  2. Keyword boosts                -> bump complexity / set intent.
  3. Router LLM for borderline prompts -> JSON override.

The router stores `intent`, `complexity`, `complexity_score` in state so
that `select_model()` downstream can pick the right model for the job.
"""
import json
import logging

from jarvis.models.ollama_client import get_router_model
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
# Router LLM for borderline prompts
# ---------------------------------------------------------------------------

_ROUTER_PROMPT_TEMPLATE = (
    "You are a request classifier for an AI assistant. Classify the user's "
    'message into exactly one intent: "general", "coding", or "complex", and '
    'one complexity: "easy", "medium", or "difficult". '
    '"coding" means writing, debugging, or explaining code, git, terminals, '
    "or scripts. \"complex\" means multi-step, architecture, deep analysis, "
    "optimization, or long-term planning. Respond with ONLY a JSON object of "
    'the form {"intent": "...", "complexity": "..."} and nothing else.\n\n'
    "User message:\n{text}"
)


def _router_prompt(text: str) -> str:
    """Build the router classifier prompt, preserving literal JSON braces."""
    return _ROUTER_PROMPT_TEMPLATE.replace("{text}", text)


def _is_borderline(text: str, complexity: str) -> bool:
    """True when the rules heuristic is indecisive enough to ask the LLM.

    A prompt is borderline when it has no decisive keyword AND its length
    heuristic lands on "medium" (the rules would default it to general with
    no strong signal either way). Clear keyword hits or easy/difficult
    lengths stay on the cheap rules path.
    """
    text_lower = text.lower()
    if any(word in text_lower for word in CODING_KEYWORDS):
        return False
    if any(phrase in text_lower for phrase in COMPLEX_KEYWORDS):
        return False
    return complexity == "medium"


def _parse_router_json(raw: str) -> dict | None:
    """Parse the router's JSON reply; return None on any malformed result."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    intent = data.get("intent")
    complexity = data.get("complexity")
    if intent not in ("general", "coding", "complex"):
        return None
    if complexity not in ("easy", "medium", "difficult"):
        return None
    return {"intent": intent, "complexity": complexity}


def _router_llm_classify(text: str, complexity: str) -> dict | None:
    """Borderline-prompt LLM classifier.

    Wired to a small local model in JSON mode (see ``get_router_model``).
    Only consulted for borderline prompts; any failure (Ollama down,
    malformed reply, disabled by config) returns None so the rules
    classifier always wins. Returns ``{"intent": ..., "complexity": ...}``
    or None ("no opinion, defer to the rules classifier").
    """
    from jarvis.config.settings import settings

    if not settings.router_llm_enabled:
        return None
    if not _is_borderline(text, complexity):
        return None
    try:
        llm = get_router_model()
        resp = llm.invoke(_router_prompt(text))
        raw = getattr(resp, "content", "")
        parsed = _parse_router_json(raw)
        if parsed is None:
            logger.info("Router LLM reply not parseable; deferring to rules")
        return parsed
    except Exception as exc:  # noqa: BLE001
        logger.warning("Router LLM classify failed (%s); deferring to rules", exc)
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

    # Router LLM hook: for borderline prompts the small router model can
    # override intent and complexity. Returns None -> rules path wins.
    llm_override = _router_llm_classify(text, complexity)
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
