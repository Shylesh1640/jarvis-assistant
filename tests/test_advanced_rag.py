"""Tests for Phase 10 :: Advanced RAG pipeline - query expansion, hybrid retrieval."""
from __future__ import annotations


from jarvis.memory.query_quality import expand_query
from jarvis.memory.retrieve import _hybrid_rerank, _compute_ranks, _cross_encoder_rerank
from jarvis.config.settings import settings


# ---------------------------------------------------------------------------
# Query expansion
# ---------------------------------------------------------------------------


def test_expand_query_returns_original_when_no_expansion():
    """Expand query returns at least the original query."""
    result = expand_query("what is the capital of France", max_variants=3)
    assert len(result) >= 1
    assert result[0] == "what is the capital of France"


def test_expand_query_generates_variants_for_known_terms():
    """Expand query generates variants when terms have known expansions."""
    # "rag" should expand to include "retrieval augmented generation"
    result = expand_query("how does rag work", max_variants=3)
    assert len(result) >= 1
    # Should include original
    assert "how does rag work" in result
    # May include expanded variant
    assert any("retrieval augmented generation" in r or "vector search" in r for r in result)


def test_expand_query_respects_max_variants():
    """Expand query respects max_variants limit."""
    result = expand_query("rag and llm and embedding", max_variants=2)
    assert len(result) <= 2


def test_expand_query_empty_input():
    """Expand query handles empty input."""
    result = expand_query("", max_variants=3)
    assert result == [""]


def test_expand_query_no_known_terms():
    """Expand query returns only original for unknown terms."""
    result = expand_query("xyzzy unknown term", max_variants=3)
    assert len(result) == 1
    assert result[0] == "xyzzy unknown term"


# ---------------------------------------------------------------------------
# Hybrid retrieval / RRF
# ---------------------------------------------------------------------------


def test_compute_ranks():
    """Ranks are computed correctly from scores."""
    scores = [0.9, 0.5, 0.7, 0.5, 0.3]
    ranks = _compute_ranks(scores)
    # 0.9 -> rank 1
    # 0.7 -> rank 2
    # 0.5 (index 1) -> rank 3 (tie)
    # 0.5 (index 3) -> rank 3 (tie)
    # 0.3 -> rank 5
    assert ranks[0] == 1
    assert ranks[2] == 2
    assert ranks[1] == 3
    assert ranks[3] == 3
    assert ranks[4] == 5


def test_compute_ranks_all_same():
    """Ranks handle all same scores."""
    scores = [0.5, 0.5, 0.5]
    ranks = _compute_ranks(scores)
    assert ranks == [1, 1, 1]


def test_compute_ranks_empty():
    """Ranks handle empty list."""
    ranks = _compute_ranks([])
    assert ranks == []


def test_hybrid_rerank_returns_candidates(monkeypatch):
    """Hybrid rerank returns ranked candidates."""
    documents = [
        "The capital of France is Paris.",
        "Paris is a city in France.",
        "Berlin is the capital of Germany.",
    ]
    distances = [0.1, 0.2, 0.8]
    metadatas = [{}, {}, {}]

    # Monkeypatch the underlying settings that the properties read
    monkeypatch.setattr("jarvis.config.settings.settings.rag_dense_weight", 0.7)
    monkeypatch.setattr("jarvis.config.settings.settings.rag_sparse_weight", 0.3)

    result = _hybrid_rerank(
        query="capital of France",
        documents=documents,
        distances=distances,
        metadatas=metadatas,
        score_threshold=None,
        hybrid_enabled=True,
    )

    assert len(result) <= 3
    assert all(isinstance(item, tuple) and len(item) == 2 for item in result)
    # First result should be about France/Paris
    assert "France" in result[0][0] or "Paris" in result[0][0]


def test_hybrid_rerank_respects_final_n():
    """Hybrid rerank respects final retrieval n."""
    documents = [f"Document {i} about France" for i in range(20)]
    distances = [i * 0.05 for i in range(20)]
    metadatas = [{} for _ in range(20)]

    original_final = getattr(settings, "rag_final_retrieval_n", 5)

    try:
        settings.rag_final_retrieval_n = 3
        result = _hybrid_rerank(
            query="France",
            documents=documents,
            distances=distances,
            metadatas=metadatas,
            score_threshold=None,
            hybrid_enabled=True,
        )
        assert len(result) == 3
    finally:
        settings.rag_final_retrieval_n = original_final


def test_hybrid_rerank_score_threshold():
    """Hybrid rerank respects score threshold."""
    documents = ["Relevant doc", "Irrelevant doc"]
    distances = [0.1, 0.9]
    metadatas = [{}, {}]

    result = _hybrid_rerank(
        query="test",
        documents=documents,
        distances=distances,
        metadatas=metadatas,
        score_threshold=0.5,  # Should filter out 0.9
        hybrid_enabled=True,
    )

    # Only first doc should remain
    assert len(result) == 1
    assert result[0][0] == "Relevant doc"


# ---------------------------------------------------------------------------
# Cross-encoder re-ranking (disabled by default)
# ---------------------------------------------------------------------------


def test_cross_encoder_rerank_fallback_when_no_model():
    """Cross-encoder rerank falls back when no model configured."""
    original_model = getattr(settings, "rag_reranking_model", "")
    original_enabled = getattr(settings, "rag_reranking_enabled", True)

    try:
        settings.rag_reranking_model = ""
        settings.rag_reranking_enabled = True

        candidates = [
            ("doc 1", {}, 0.5),
            ("doc 2", {}, 0.3),
        ]

        result = _cross_encoder_rerank("query", candidates)
        # Should return unchanged when no model
        assert result == candidates
    finally:
        settings.rag_reranking_model = original_model
        settings.rag_reranking_enabled = original_enabled


def test_cross_encoder_rerank_disabled_when_flag_off():
    """Cross-encoder rerank is skipped when disabled."""
    original_enabled = getattr(settings, "rag_reranking_enabled", True)

    try:
        settings.rag_reranking_enabled = False

        candidates = [
            ("doc 1", {}, 0.5),
            ("doc 2", {}, 0.3),
        ]

        result = _cross_encoder_rerank("query", candidates)
        assert result == candidates
    finally:
        settings.rag_reranking_enabled = original_enabled


# ---------------------------------------------------------------------------
# Settings validation for Phase 10
# ---------------------------------------------------------------------------


def test_settings_dense_sparse_weight_validation(monkeypatch):
    """Settings validation catches invalid dense/sparse weights."""
    monkeypatch.setattr("jarvis.config.settings.settings.rag_hybrid_retrieval_enabled", True)
    monkeypatch.setattr("jarvis.config.settings.settings.rag_dense_weight", 1.5)
    monkeypatch.setattr("jarvis.config.settings.settings.rag_sparse_weight", 0.3)

    from jarvis.config.settings import validate_runtime_settings
    warnings = validate_runtime_settings()
    assert any("RAG_DENSE_WEIGHT must be in [0, 1]" in w for w in warnings)


def test_settings_dense_sparse_weight_sum_validation(monkeypatch):
    """Settings validation catches zero sum of weights."""
    monkeypatch.setattr("jarvis.config.settings.settings.rag_hybrid_retrieval_enabled", True)
    monkeypatch.setattr("jarvis.config.settings.settings.rag_dense_weight", 0.0)
    monkeypatch.setattr("jarvis.config.settings.settings.rag_sparse_weight", 0.0)

    from jarvis.config.settings import validate_runtime_settings
    warnings = validate_runtime_settings()
    assert any("RAG_DENSE_WEIGHT + RAG_SPARSE_WEIGHT must be > 0" in w for w in warnings)


def test_settings_query_expansion_variants_validation(monkeypatch):
    """Settings validation catches invalid max_variants."""
    monkeypatch.setattr("jarvis.config.settings.settings.rag_query_expansion_enabled", True)
    monkeypatch.setattr("jarvis.config.settings.settings.rag_query_expansion_max_variants", 0)

    from jarvis.config.settings import validate_runtime_settings
    warnings = validate_runtime_settings()
    assert any("RAG_QUERY_EXPANSION_MAX_VARIANTS must be >= 1" in w for w in warnings)


def test_settings_rerank_params_validation(monkeypatch):
    """Settings validation catches invalid rerank params."""
    monkeypatch.setattr("jarvis.config.settings.settings.rag_reranking_enabled", True)
    monkeypatch.setattr("jarvis.config.settings.settings.rag_initial_retrieval_k", 0)

    from jarvis.config.settings import validate_runtime_settings
    warnings = validate_runtime_settings()
    assert any("RAG_INITIAL_RETRIEVAL_K must be >= 1" in w for w in warnings)


def test_settings_final_n_le_initial_k(monkeypatch):
    """Settings validation ensures final_n <= initial_k."""
    monkeypatch.setattr("jarvis.config.settings.settings.rag_reranking_enabled", True)
    monkeypatch.setattr("jarvis.config.settings.settings.rag_initial_retrieval_k", 10)
    monkeypatch.setattr("jarvis.config.settings.settings.rag_final_retrieval_n", 20)

    from jarvis.config.settings import validate_runtime_settings
    warnings = validate_runtime_settings()
    assert any("RAG_FINAL_RETRIEVAL_N must be <= RAG_INITIAL_RETRIEVAL_K" in w for w in warnings)