"""RAG query classification, rewriting, and expansion.

Phase 5 :: RAG retrieval quality.
Phase 10 :: Advanced RAG pipeline - query expansion.

Two small, pure, rules-based helpers sit in front of retrieval:
* ``is_smalltalk`` — recognises greetings / social filler so the RAG
  layer can skip an expensive embedding + search that would return
  nothing useful.
* ``rewrite_retrieval_query`` — strips conversational framing ("can you
  tell me about...", "what is...") and trailing filler so the embedding
  model gets the *informational core* of the question. Long prompts are
  trimmed to the part that carries the most signal.
* ``expand_query`` — generates query variants (synonyms, related concepts,
  abbreviated forms) for expanded retrieval coverage.

All are deliberately deterministic and LLM-free: they add minimal latency
and are easy to unit test. Anything ambiguous is passed through
unchanged (fail-open), so we never *lose* retrieval signal.
"""
from __future__ import annotations

import re
from functools import lru_cache

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


# ---------------------------------------------------------------------------
# Query expansion (Phase 10)
# ---------------------------------------------------------------------------

# Simple synonym/related-term mappings for common technical terms.
# This is a lightweight, LLM-free approach. For production use, consider
# integrating a proper thesaurus or word embedding nearest-neighbors.
_QUERY_EXPANSION_MAP: dict[str, set[str]] = {
    "rag": {"retrieval augmented generation", "retrieval-augmented generation", "vector search"},
    "llm": {"large language model", "language model", "gpt"},
    "embedding": {"vector representation", "vector embedding", "semantic vector"},
    "chroma": {"chromadb", "vector database", "vector store", "vector db"},
    "ollama": {"local llm", "local model", "self-hosted llm"},
    "bm25": {"keyword search", "sparse retrieval", "lexical search"},
    "hybrid": {"combined search", "fusion retrieval", "dense sparse fusion"},
    "rerank": {"re-rank", "reranking", "cross encoder", "cross-encoder"},
    "api": {"rest api", "restful api", "http api", "web api"},
    "cli": {"command line interface", "command-line", "terminal"},
    "ui": {"user interface", "frontend", "gui"},
    "db": {"database", "datastore", "data store"},
    "config": {"configuration", "settings", "parameters"},
    "auth": {"authentication", "authorization", "login", "sign in"},
    "jwt": {"json web token", "token", "bearer token"},
    "sql": {"structured query language", "relational database"},
    "nosql": {"non-relational database", "document database", "key-value store"},
    "docker": {"container", "containerization", "container platform"},
    "kubernetes": {"k8s", "container orchestration", "container platform"},
    "ci": {"continuous integration", "build pipeline"},
    "cd": {"continuous deployment", "continuous delivery", "deployment pipeline"},
    "ml": {"machine learning", "artificial intelligence", "ai"},
    "nlp": {"natural language processing", "text processing"},
    "cv": {"computer vision", "image processing"},
    "rl": {"reinforcement learning", "reward learning"},
    "gan": {"generative adversarial network", "generative model"},
    "vae": {"variational autoencoder", "generative model"},
    "transformer": {"attention model", "self-attention", "encoder-decoder"},
    "bert": {"bidirectional encoder", "pretrained language model"},
    "gpt": {"generative pretrained transformer", "autoregressive model"},
    "agent": {"ai agent", "autonomous agent", "llm agent"},
    "tool": {"function calling", "tool use", "api call"},
    "prompt": {"prompt engineering", "prompting", "instruction"},
    "fine-tune": {"fine tuning", "finetuning", "model adaptation"},
    "lora": {"low-rank adaptation", "parameter-efficient fine-tuning"},
    "quantization": {"quantisation", "model compression", "weight quantization"},
    "distillation": {"knowledge distillation", "model compression"},
}


def _tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, drop stopwords."""
    tokens = re.findall(r"[a-zA-Z0-9_]+", text.lower())
    return [t for t in tokens if len(t) > 1]


@lru_cache(maxsize=128)
def _expand_single_term(term: str) -> set[str]:
    """Get expansion terms for a single token (cached)."""
    return _QUERY_EXPANSION_MAP.get(term, set())


def expand_query(query: str, max_variants: int = 3) -> list[str]:
    """Generate query variants for expanded retrieval.

    Strategy:
    1. Start with the rewritten original query.
    2. Tokenize and find expansion terms for each token.
    3. Generate variants by replacing tokens with their expansions.
    4. Deduplicate and return up to max_variants queries (including original).

    Fail-open: returns original query on any error or when no expansions found.
    """
    if not query or not query.strip():
        return [""]

    try:
        # Start with the cleaned query
        variants = [query.strip()]
        tokens = _tokenize(query)

        # Find tokens that have expansions
        expansion_candidates = []
        for token in tokens:
            expansions = _expand_single_term(token)
            if expansions:
                expansion_candidates.append((token, expansions))

        if not expansion_candidates:
            return variants

        # Generate variants by substituting expanded terms
        # Simple approach: replace one token at a time with its best expansion
        for token, expansions in expansion_candidates:
            if len(variants) >= max_variants:
                break
            # Use the first/best expansion
            best_expansion = sorted(expansions, key=len)[0]  # shortest first
            if best_expansion != token and best_expansion not in query:
                variant = query.replace(token, best_expansion)
                if variant not in variants:
                    variants.append(variant)

        # Also try adding a related term as a suffix
        if len(variants) < max_variants and expansion_candidates:
            token, expansions = expansion_candidates[0]
            best_expansion = sorted(expansions, key=len)[0]
            variant = f"{query} {best_expansion}"
            if variant not in variants:
                variants.append(variant)

        return variants[:max_variants]
    except Exception:
        # Fail-open: return original query
        return [query.strip()]


__all__ = ["is_smalltalk", "rewrite_retrieval_query", "expand_query"]