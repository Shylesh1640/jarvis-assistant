"""Tests for hybrid retrieval (vector + BM25 reranking, collection filter)."""
from __future__ import annotations

from unittest.mock import MagicMock

from jarvis.memory.retrieve import Collection, _bm25_scores, _rerank, _tokenize, query_context


def test_tokenize_drops_stopwords_and_punct():
    tokens = _tokenize("The quick, brown fox! jumps over the lazy_dog")
    assert "the" not in tokens
    assert "quick" in tokens
    assert "lazy_dog" in tokens


def test_bm25_ranks_keyword_docs_higher():
    docs = [
        "the cat sat on the mat",
        "python is a programming language",
        "python programming and lets talk about programming with python",
    ]
    scores = _bm25_scores("python programming", docs)
    assert scores[2] > scores[1] > scores[0]


def test_rerank_uses_keyword_boost():
    # Two docs with identical distance → keyword overlap decides order.
    docs = ["machine learning today", "cooking recipes for pasta"]
    distances = [0.2, 0.2]
    metas = [{"source": "a"}, {"source": "b"}]
    out = _rerank("machine learning", docs, distances, metas, None)
    assert out[0][0] == "machine learning today"


def test_rerank_zero_keyword_weight_is_pure_vector(monkeypatch):
    monkeypatch.setattr("jarvis.memory.retrieve.settings.rerank_keyword_weight", 0.0)
    docs = ["zzz irrelevant", "match keyword here"]
    distances = [0.05, 0.95]  # first is vector-closest
    metas = [{}, {}]
    out = _rerank("keyword", docs, distances, metas, None)
    # Assert first result is the vector-nearest document.
    assert [d for d, _ in out][0] == "zzz irrelevant"


def test_rerank_pure_keyword_uses_bm25_order(monkeypatch):
    monkeypatch.setattr("jarvis.memory.retrieve.settings.rerank_keyword_weight", 1.0)
    # Far document mentions the query term; near one is irrelevant.
    docs = ["zzz irrelevant", "match keyword here clearly"]
    distances = [0.05, 0.95]
    out = _rerank("keyword", docs, distances, [{}, {}], None)
    assert [d for d, _ in out][0] == "match keyword here clearly"


def test_rerank_applies_threshold(monkeypatch):
    monkeypatch.setattr("jarvis.memory.retrieve.settings.rerank_keyword_weight", 0.0)
    docs = ["close match", "far away"]
    distances = [0.3, 0.9]
    out = _rerank("match", docs, distances, [{}, {}], score_threshold=0.5)
    assert [d for d, _ in out] == ["close match"]


def test_query_context_filters_by_collection(monkeypatch):
    col = MagicMock()
    col.query.return_value = {
        "documents": [["doc a", "doc b"]],
        "distances": [[0.1, 0.2]],
        "metadatas": [[{"source": "a.txt", "chunk_id": "c1"}, {"source": "b.txt", "chunk_id": "c2"}]],
    }
    monkeypatch.setattr("jarvis.memory.retrieve.get_collection", lambda: col)
    emb = MagicMock()
    emb.embed_query = lambda q: [0.1, 0.2, 0.3]
    monkeypatch.setattr("jarvis.memory.retrieve.get_embedding_function", lambda: emb)
    monkeypatch.setattr("jarvis.config.settings.settings.rerank_keyword_weight", 0.0)

    query_context("hello", k=2, collection=Collection.CODE)

    args, kwargs = col.query.call_args
    assert kwargs["where"] == {"kind": "code"}


def test_query_context_returns_sources_with_page_section(monkeypatch):
    col = MagicMock()
    col.query.return_value = {
        "documents": [["hello world text"]],
        "distances": [[0.1]],
        "metadatas": [[{"source": "book.pdf", "chunk_id": "abc", "page": 3, "section": "page-3"}]],
    }
    monkeypatch.setattr("jarvis.memory.retrieve.get_collection", lambda: col)
    emb = MagicMock()
    emb.embed_query = lambda q: [0.1]
    monkeypatch.setattr("jarvis.memory.retrieve.get_embedding_function", lambda: emb)
    monkeypatch.setattr("jarvis.config.settings.settings.rerank_keyword_weight", 0.0)

    ctx, sources = query_context("hello", k=1, with_sources=True)
    assert "(p.3)" in ctx
    assert "[page-3]" in ctx
    assert sources[0]["source"] == "book.pdf"
    assert sources[0]["chunk_id"] == "abc"


def test_query_context_empty_returns_empty(monkeypatch):
    col = MagicMock()
    col.query.return_value = {"documents": [[]], "distances": [[]], "metadatas": [[]]}
    monkeypatch.setattr("jarvis.memory.retrieve.get_collection", lambda: col)
    emb = MagicMock()
    emb.embed_query = lambda q: [0.1]
    monkeypatch.setattr("jarvis.memory.retrieve.get_embedding_function", lambda: emb)
    assert query_context("nothing") == ""