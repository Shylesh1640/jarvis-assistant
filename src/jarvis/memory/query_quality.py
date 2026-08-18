"""RAG query classification and rewriting.

Phase 5 :: RAG retrieval quality.

Two small, pure, rules-based helpers sit in front of retrieval:

* ``is_smalltalk`` — recognises greetings / social filler so the RAG
  layer can skip an expensive embedding + search that would return
  nothing useful.
* ``rewrite_retrieval_query`` — strips conversational framing ("can you
  tell me about...", "what is...") and trailing filler so the embedding
  model gets the *informational core* of the question. Long prompts are
  trimmed to the part that carries the most signal.

Both are deliberately deterministic and LLM-free: they add zero latency
and are easy to unit test. Anything ambiguous is passed through
unchanged (fail-open), so we never *lose* retrieval signal.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Small-talk detection
# ---------------------------------------------------------------------------

# Greeting / politeness tokens that signal the user is not asking for
# information. Matching is case-insensitive and word-boundary based.
_SMALLTALK_TOKENS = {
    "hi", "hello", "hey", "yo", "howdy", "hiya", "good morning",
    "good afternoon", "good evening", "greetings",
    "thanks", "thank", "thank you", "thankyou", "ty", "thx", "cheers",
    "bye", "goodbye", "see you", "cya", "see ya",
    "ok", "okay", "sure", "great", "awesome", "nice", "cool",
    "well done", "nice work", "great job", "good job",
    "yes", "no", "nope", "yep", "yeah", "nah",
    "how are you", "how's it going", "hows it going", "how is it going",
    "what's up", "whats up", "sup", "howdy do",
}

# A message is smalltalk when every content word belongs to the set
# above (plus very short generic words like "just", "really", "i").
_SMALLTALK_STOP = {
    "i", "im", "i'm", "just", "really", "very", "so", "and", "there",
    "it's", "its", "that's", "thats", "the", "a", "an", "is", "are",
    "say", "saying", "checking", "want", "wanted", "wanna", "gotta",
    "you", "u", "you're", "your", "ya",
}

_GREETING_ONLY = re.compile(
    r"^(hi|hello|hey|yo|howdy|hiya|greetings|good (morning|afternoon|evening)"
    r"|how are you|how's it going|hows it going|how is it going"
    r"|what's up|whats up|sup|goodbye|bye|see you|cya|see ya"
    r"|thanks|thank you|thankyou|thx|ty|cheers|ok|okay"
    r"|great job|good job|well done|nice work|great work|awesome)[!.,?]*$",
    re.IGNORECASE,
)

# Exact normalized phrases that are unambiguous smalltalk (checked before
# the word-by-word fallback, which is too brittle for these).
_SMALLTALK_PHRASES = {
    "hi just saying hello",
    "hi there",
    "hey there",
    "hello there",
    "hey how are you",
    "hello how are you",
    "hi how are you",
    "just saying hello",
    "just saying hi",
    "hi there how are you",
    "hey there how's it going",
    "hi there how's it going",
    "how's it going",
    "how is it going",
    "how are you doing",
    "how are you doing today",
}


def _normalize(text: str) -> str:
    return " ".join(re.findall(r"[a-z']+", text.lower()))


def is_smalltalk(text: str) -> bool:
    """Return True when *text* looks like greeting / filler, not a query.

    Fail-open: anything with a content word outside the smalltalk set
    (or longer than a couple of short sentences) returns False so it
    still goes through retrieval.
    """
    if not text or not text.strip():
        return True
    cleaned = text.strip()
    if len(cleaned) > 80:
        return False
    if _GREETING_ONLY.match(cleaned):
        return True
    if _normalize(cleaned) in _SMALLTALK_PHRASES:
        return True

    words = re.findall(r"[a-z']+", cleaned.lower())
    if not words or len(words) > 12:
        return False
    meaningful = [w for w in words if w not in _SMALLTALK_STOP]
    if not meaningful:
        return False
    # "how" is allowed only as part of "how are you" — handle it as a
    # compound check: a lone "how" (e.g. "how does X work") is a query.
    return all(
        (w in _SMALLTALK_TOKENS or w.rstrip("!.,?") in _SMALLTALK_TOKENS)
        or w == "how" and _is_how_are_you(cleaned)
        for w in meaningful
    )


def _is_how_are_you(text: str) -> bool:
    lowered = " ".join(text.lower().split())
    return any(
        phrase in lowered
        for phrase in ("how are you", "how's it going", "hows it going", "how is it going")
    )


# ---------------------------------------------------------------------------
# Query rewriting
# ---------------------------------------------------------------------------

# Leading conversational framing to strip before embedding. Multi-word
# entries are matched first so "can you explain" wins over "explain".
_LEADING_FRAMING = [
    "can you tell me about",
    "could you tell me about",
    "can you explain",
    "could you explain",
    "can you tell me",
    "could you tell me",
    "i want to know about",
    "i would like to know about",
    "i'd like to know about",
    "i want to learn about",
    "i would like to learn about",
    "i'd like to learn about",
    "do you know about",
    "do you know anything about",
    "tell me about",
    "tell me",
    "explain to me",
    "explain",
    "what can you tell me about",
    "what do you know about",
    "what is the deal with",
    "what are the details on",
]

# Trailing politeness filler to trim ("please", "thanks", "thank you").
_TRAILING_POLITENESS = re.compile(
    r"(?i)(\s+(please|thanks|thank you|thx|ty|cheers))[!.]*$"
)

# A question mark ends the informative part; anything after is noise.
_FIRST_QUESTION_MARK = re.compile(r"[?].*$", re.DOTALL)


def _strip_leading_framing(text: str) -> str:
    lowered = text.lower().lstrip()
    for phrase in sorted(_LEADING_FRAMING, key=len, reverse=True):
        if lowered.startswith(phrase):
            remainder = text[len(phrase):].lstrip(" ,:-")
            if remainder:
                return remainder.capitalize() if remainder[0].islower() else remainder
            return ""
    return text


def rewrite_retrieval_query(query: str) -> str:
    """Return the informational core of *query* for embedding.

    * Trims whitespace and trailing politeness.
    * Drops leading conversational framing ("can you tell me about ...").
    * Truncates at the first question mark.
    * Long prompts are cut to the first ~300 characters (a retrieval
      query that long is dominated by framing anyway).

    Fail-open: unchanged input is returned whenever the rewrite would
    produce an empty or degraded string.
    """
    text = (query or "").strip()
    if not text:
        return ""
    # Keep only text up to (and including) the first question mark; the
    # framing sentence of a long question rarely helps the embedding.
    text = _FIRST_QUESTION_MARK.sub("", text)

    stripped = _strip_leading_framing(text)
    stripped = _TRAILING_POLITENESS.sub("", stripped).strip()

    if not stripped:
        stripped = text.strip("? ").strip()
    if not stripped:
        return ""

    # Hard ceiling so a giant prompt can't be embedded verbatim.
    if len(stripped) > 300:
        cut = stripped[:300].rsplit(" ", 1)[0]
        return (cut or stripped[:300]).strip()
    return stripped


__all__ = ["is_smalltalk", "rewrite_retrieval_query"]