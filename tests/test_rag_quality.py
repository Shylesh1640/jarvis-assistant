"""Tests for Phase 5 RAG retrieval quality: query classification, rewriting,
hybrid weights, rerank toggle, per-source limit, dedup, and source enrichment.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from jarvis.memory.query_quality import is_smalltalk, rewrite_retrieval_query
from jarvis.memory.retrieve import Collection, _rerank, query_context


# ---------------------------------------------------------------------------
# Query classification (small-talk)
# ---------------------------------------------------------------------------

def test_smalltalk_greetings():
    for msg in ("hi", "Hello!", "Hey", "good morning", "how are you?", "yo",
                "hiya", "good afternoon"):
        assert is_smalltalk(msg), f"expected {msg!r} to be smalltalk"


def test_smalltalk_politeness():
    for msg in ("thanks", "thank you", "cheers", "ok", "okay", "great job",
                "well done", "bye"):
        assert is_smalltalk(msg), f"expected {msg!r} to be smalltalk"


def test_smalltalk_sentence_with_filler():
    assert is_smalltalk("hi, just saying hello")
    assert is_smalltalk("hey there, how's it going")


def test_non_smalltalk_real_questions():
    for msg in ("what is the capital of France", "explain RAG", "write a test",
                "how do I fix this error", "what are the details on project X"):
        assert not is_smalltalk(msg), f"expected {msg!r} NOT to be smalltalk"


def test_smalltalk_fails_open_on_content_words():
    # Any content word outside the smalltalk set means "not smalltalk".
    assert not is_smalltalk("hi, what is the weather")


def test_smalltalk_empty_and_long():
    assert is_smalltalk("")
    assert is_smalltalk("   ")
    # A long rambling message with a real question is never smalltalk.
    long = "hi there, " + "can you tell me about " * 20 + "the project?"
    assert not is_smalltalk(long)


# ---------------------------------------------------------------------------
# Query rewriting
# ---------------------------------------------------------------------------

def test_rewrite_strips_leading_framing():
    assert rewrite_retrieval_query("Can you tell me about vector databases?") \
        == "Vector databases"
    assert rewrite_retrieval_query("Tell me about how transformers work") \
        == "How transformers work"


def test_rewrite_strips_politeness():
    assert rewrite_retrieval_query("explain RAG please") == "RAG"


def test_rewrite_truncates_at_question_mark():
    out = rewrite_retrieval_query("What is RAG? I also want to know about it later.")
    assert out == "What is RAG"


def test_rewrite_keeps_informative_query():
    q = "How does hybrid retrieval combine BM25 and vector similarity"
    assert rewrite_retrieval_query(q) == q


def test_rewrite_fail_open_on_empty():
    assert rewrite_retrieval_query("") == ""
    assert rewrite_retrieval_query("   ") == ""


# ---------------------------------------------------------------------------
# Hybrid weights (two-weight mode + legacy fallback)
# ---------------------------------------------------------------------------

def test_rerank_legacy_keyword_weight_still_works(monkeypatch):
    """When the new two-weight fields are unset, rerank_keyword_weight drives."""
    monkeypatch.setattr("jarvis.memory.retrieve.settings.rerank_keyword_weight", 1.0)
    docs = ["zzz irrelevant", "match keyword here clearly"]
    distances = [0.05, 0.95]
    out = _rerank("keyword", docs, distances, [{}, {}], None)
    assert [d for d, _ in out][0] == "match keyword here clearly"


def test_rerank_two_weight_mode(monkeypatch):
    monkeypatch.setattr("jarvis.memory.retrieve.settings.rerank_keyword_weight", 0.5)
    monkeypatch.setattr("jarvis.memory.retrieve.settings.rag_vector_weight", 1.0)
    monkeypatch.setattr("jarvis.memory.retrieve.settings.rag_keyword_weight", 0.0)
    # Explicit two-weight config (pure vector) overrides the legacy knob.
    docs = ["zzz irrelevant", "match keyword here"]
    distances = [0.05, 0.95]
    out = _rerank("keyword", docs, distances, [{}, {}], None)
    assert [d for d, _ in out][0] == "zzz irrelevant"


def test_rerank_toggle_disabled_is_pure_vector(monkeypatch):
    monkeypatch.setattr("jarvis.memory.retrieve.settings.rag_rerank_enabled", False)
    # Even with keyword-heavy config, disabling the rerank means vector wins.
    monkeypatch.setattr("jarvis.memory.retrieve.settings.rerank_keyword_weight", 1.0)
    docs = ["zzz irrelevant", "match keyword here clearly"]
    distances = [0.05, 0.95]
    out = _rerank("keyword", docs, distances, [{}, {}], None)
    assert [d for d, _ in out][0] == "zzz irrelevant"


# ---------------------------------------------------------------------------
# Per-source limit + dedup + source enrichment
# ---------------------------------------------------------------------------

def _stub_collection(docs, distances, metas):
    col = MagicMock()
    col.query.return_value = {
        "documents": [docs],
        "distances": [distances],
        "metadatas": [metas],
    }
    return col


def _patch_retrieve(monkeypatch, col):
    monkeypatch.setattr("jarvis.memory.retrieve.get_collection", lambda: col)
    emb = MagicMock()
    emb.embed_query = lambda q: [0.1]
    monkeypatch.setattr("jarvis.memory.retrieve.get_embedding_function", lambda: emb)


def test_per_source_limit_caps_chunks(monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.retrieval_per_source_limit", 1)
    monkeypatch.setattr("jarvis.memory.retrieve.settings.rerank_keyword_weight", 0.0)
    col = _stub_collection(
        ["doc a one", "doc a two", "doc b one"],
        [0.1, 0.2, 0.3],
        [{"source": "a.md"}, {"source": "a.md"}, {"source": "b.md"}],
    )
    _patch_retrieve(monkeypatch, col)
    ctx, sources = query_context("query", k=5, with_sources=True)
    srcs = [s["source"] for s in sources]
    assert srcs.count("a.md") == 1
    assert srcs.count("b.md") == 1


def test_query_context_dedups_chunks(monkeypatch):
    monkeypatch.setattr("jarvis.memory.retrieve.settings.rerank_keyword_weight", 0.0)
    monkeypatch.setattr("jarvis.config.settings.settings.retrieval_per_source_limit", 0)
    col = _stub_collection(
        ["dup text", "dup text", "other"],
        [0.1, 0.1, 0.3],
        [
            {"source": "a.md", "chunk_id": "c1"},
            {"source": "a.md", "chunk_id": "c1"},
            {"source": "b.md", "chunk_id": "c2"},
        ],
    )
    _patch_retrieve(monkeypatch, col)
    ctx, sources = query_context("query", k=5, with_sources=True)
    # Duplicate chunk_id appears only once in the sources.
    ids = [s["chunk_id"] for s in sources]
    assert ids.count("c1") == 1
    assert "dup text" in ctx


def test_sources_include_page_section_and_doc(monkeypatch):
    monkeypatch.setattr("jarvis.memory.retrieve.settings.rerank_keyword_weight", 0.0)
    col = _stub_collection(
        ["hello world text"],
        [0.1],
        [{"source": "book.pdf", "chunk_id": "abc", "page": 3, "section": "page-3"}],
    )
    _patch_retrieve(monkeypatch, col)
    ctx, sources = query_context("hello", k=1, with_sources=True)
    assert "(p.3)" in ctx
    assert "[page-3]" in ctx
    hit = sources[0]
    assert hit["source"] == "book.pdf"
    assert hit["chunk_id"] == "abc"
    assert hit["page"] == 3
    assert hit["section"] == "page-3"
    assert hit["doc"] == "hello world text"


def test_query_context_filters_by_collection_kind(monkeypatch):
    col = _stub_collection(
        ["doc a"],
        [0.1],
        [{"source": "a.txt", "chunk_id": "c1"}],
    )
    _patch_retrieve(monkeypatch, col)
    monkeypatch.setattr("jarvis.memory.retrieve.settings.rerank_keyword_weight", 0.0)
    query_context("hello", k=1, collection=Collection.CODE)
    args, kwargs = col.query.call_args
    assert kwargs["where"] == {"kind": "code"}
