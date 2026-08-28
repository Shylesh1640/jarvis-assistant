"""Functions to query Chroma and return a context string.

Advanced RAG Pipeline (Phase 10):
- Hybrid retrieval with Reciprocal Rank Fusion (RRF):
  * Dense retrieval (Chroma cosine similarity)
  * Sparse retrieval (BM25 keyword scoring)
  * RRF fusion for robust ranking
- Query expansion for broader coverage
- Optional cross-encoder re-ranking

Reranking happens entirely in-process (no external reranker model by default)
so it adds minimal latency and zero extra deps. The hybrid weights come from
``settings.effective_dense_weight`` / ``settings.effective_sparse_weight``
with fallback to legacy weights. When ``settings.rag_hybrid_retrieval_enabled``
is False, falls back to dense-only retrieval.
"""
from __future__ import annotations

import math
from collections import Counter
from enum import Enum

from jarvis.config.settings import settings
from jarvis.memory.query_quality import expand_query, rewrite_retrieval_query
from jarvis.memory.store import get_collection, get_embedding_function


class Collection(str, Enum):
    """Logical collection partitions within the single physical Chroma collection.

    Stored in chunk metadata under the ``kind`` key so a query can filter
    to one partition (docs / memory / code / conversations) or query all.
    """

    DOCS = "docs"
    MEMORY = "memory"
    CODE = "code"
    CONVERSATIONS = "conversations"


def query_context(
    query: str,
    k: int = 4,
    score_threshold: float | None = None,
    *,
    with_sources: bool = False,
    collection: Collection | None = None,
) -> str | tuple[str, list[dict[str, str]]]:
    """Embed *query*, search Chroma, and return a formatted context block.

    Parameters
    ----------
    query:
        The user's question or search text.
    k:
        Number of top results to retrieve (final results after re-ranking).
    score_threshold:
        If set, only results with a distance **below** this value are kept
        (lower distance = more similar).
    with_sources:
        When True, return ``(context_block, sources)`` where ``sources`` is
        a list of ``{"source", "chunk_id", "doc"}`` dicts for UI citations.
    collection:
        Restrict to a logical partition (docs/memory/code/conversations).
        When None, queries all partitions.

    Returns
    -------
    A string suitable for injecting into an LLM prompt as retrieved context,
    or an empty string if nothing relevant is found. With ``with_sources``
    the return is a (string, list) tuple.
    """
    # Phase 10: Query expansion for broader retrieval coverage
    expanded_queries = _get_expanded_queries(query)

    all_documents: list[str] = []
    all_distances: list[float] = []
    all_metadatas: list[dict] = []

    emb_fn = get_embedding_function()
    col = get_collection()

    # Initial retrieval k for hybrid pipeline
    initial_k = getattr(settings, "rag_initial_retrieval_k", 50)

    # Retrieve for each expanded query and collect all results
    for exp_query in expanded_queries:
        rewritten = rewrite_retrieval_query(exp_query)
        if not rewritten:
            continue

        query_embedding = emb_fn.embed_query(rewritten)
        where = {"kind": collection.value} if collection else None

        results = col.query(
            query_embeddings=[query_embedding],
            n_results=initial_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )

        if results["documents"] and results["documents"][0]:
            all_documents.extend(results["documents"][0])
            all_distances.extend((results.get("distances") or [[]])[0])
            all_metadatas.extend((results.get("metadatas") or [[]])[0])

    # Deduplicate by chunk_id
    seen_chunks: set[str] = set()
    deduped_docs: list[str] = []
    deduped_distances: list[float] = []
    deduped_metas: list[dict] = []

    for doc, dist, meta in zip(all_documents, all_distances, all_metadatas):
        chunk_id = (meta or {}).get("chunk_id", "")
        if chunk_id and chunk_id in seen_chunks:
            continue
        seen_chunks.add(chunk_id)
        deduped_docs.append(doc)
        deduped_distances.append(dist)
        deduped_metas.append(meta)

    if not deduped_docs:
        return ("", []) if with_sources else ""

    # Hybrid retrieval with RRF fusion
    hybrid_enabled = bool(getattr(settings, "rag_hybrid_retrieval_enabled", True))
    reranked = _hybrid_rerank(
        query=query,
        documents=deduped_docs,
        distances=deduped_distances,
        metadatas=deduped_metas,
        score_threshold=score_threshold,
        hybrid_enabled=hybrid_enabled,
    )

    # Per-source cap: keep at most `settings.retrieval_per_source_limit`
    # chunks per distinct source so one large document can't crowd out the
    # rest (0 = unlimited, legacy behaviour).
    per_source = max(0, int(getattr(settings, "retrieval_per_source_limit", 0)))
    seen_sources: Counter[str] = Counter()
    # Dedup by (source, chunk_id) so a chunk surfaced twice (e.g. via the
    # overlap splitter) is only injected once.
    seen_chunks: set[tuple[str, str]] = set()

    gathered: list[str] = []
    sources: list[dict[str, str]] = []

    for doc, meta in reranked:
        src = (meta or {}).get("source") or "Doc"
        chunk_id = (meta or {}).get("chunk_id") or ""
        if per_source and seen_sources[src] >= per_source:
            continue
        key = (src, chunk_id)
        if chunk_id and key in seen_chunks:
            continue
        seen_sources[src] += 1
        if chunk_id:
            seen_chunks.add(key)

        page = (meta or {}).get("page")
        section = (meta or {}).get("section")
        score = (meta or {}).get("score")
        extra = ""
        if page:
            extra += f" (p.{page})"
        if section:
            extra += f" [{section}]"
        tag = f"[{src}{extra}]" if extra else f"[{src}]"
        gathered.append(f"{tag} {doc}")
        hit = {"source": src, "chunk_id": chunk_id, "doc": doc}
        if page is not None:
            hit["page"] = page
        if section:
            hit["section"] = section
        if score is not None:
            hit["score"] = score
        sources.append(hit)

        if len(gathered) >= k:
            break

    if not gathered:
        return ("", []) if with_sources else ""

    return ("\n\n".join(gathered), sources) if with_sources else "\n\n".join(gathered)


def _get_expanded_queries(query: str) -> list[str]:
    """Get expanded queries if query expansion is enabled."""
    if not bool(getattr(settings, "rag_query_expansion_enabled", True)):
        return [query]

    max_variants = getattr(settings, "rag_query_expansion_max_variants", 3)
    return expand_query(query, max_variants=max_variants)


# ---------------------------------------------------------------------------
# Hybrid Retrieval with Reciprocal Rank Fusion (RRF)
# ---------------------------------------------------------------------------

def _hybrid_rerank(
    query: str,
    documents: list[str],
    distances: list[float],
    metadatas: list[dict],
    score_threshold: float | None,
    hybrid_enabled: bool,
) -> list[tuple[str, dict]]:
    """Combine dense (vector) + sparse (BM25) retrieval using RRF.

    RRF formula: score = sum(1 / (k + rank)) for each retrieval system
    where k is a constant (typically 60).

    This is more robust than linear combination because it doesn't require
    score normalization and handles different score distributions gracefully.
    """
    if not documents:
        return []

    # Dense scores from vector distances (1 - normalized_distance)
    if distances:
        max_d = max(distances) if max(distances) > 0 else 1.0
        min_d = min(distances) if min(distances) >= 0 else 0.0
        dense_scores = [_norm(min_d, max_d, d) for d in distances]
    else:
        dense_scores = [1.0] * len(documents)

    # Sparse scores from BM25
    sparse_scores = _bm25_scores(query, documents)
    if sparse_scores:
        max_kw = max(sparse_scores) if max(sparse_scores) > 0 else 1.0
        sparse_scores = [s / max_kw for s in sparse_scores]
    else:
        sparse_scores = [0.0] * len(documents)

    # Compute ranks for RRF (1 = best rank)
    dense_ranks = _compute_ranks(dense_scores)
    sparse_ranks = _compute_ranks(sparse_scores)

    # RRF fusion
    rrf_k = 60  # standard RRF constant
    dense_weight = float(getattr(settings, "effective_dense_weight", 0.7))
    sparse_weight = float(getattr(settings, "effective_sparse_weight", 0.3))

    out: list[tuple[str, dict, float]] = []
    for i, doc in enumerate(documents):
        # Skip if over score_threshold (distance-based)
        if score_threshold is not None and i < len(distances) and distances[i] > score_threshold:
            continue

        # RRF score: weighted sum of reciprocal ranks
        rrf_score = (
            dense_weight * (1.0 / (rrf_k + dense_ranks[i]))
            + sparse_weight * (1.0 / (rrf_k + sparse_ranks[i]))
        )
        out.append((doc, metadatas[i] if i < len(metadatas) else {}, rrf_score))

    # Sort by RRF score descending
    out.sort(key=lambda t: t[2], reverse=True)

    # Optional cross-encoder re-ranking
    if bool(getattr(settings, "rag_reranking_enabled", True)):
        out = _cross_encoder_rerank(query, out)

    # Final top-n
    final_n = getattr(settings, "rag_final_retrieval_n", 5)
    out = out[:final_n]

    return [(d, m) for d, m, _ in out]


# ---------------------------------------------------------------------------
# Backward compatibility alias for tests
# ---------------------------------------------------------------------------

def _rerank(
    query: str,
    documents: list[str],
    distances: list[float],
    metadatas: list[dict],
    score_threshold: float | None,
) -> list[tuple[str, dict]]:
    """Legacy rerank function for backward compatibility with tests.

    Uses the old linear combination approach with effective_vector_weight
    and effective_keyword_weight.
    """
    if not documents:
        return []

    if distances:
        max_d = max(distances) if max(distances) > 0 else 1.0
        min_d = min(distances) if min(distances) >= 0 else 0.0
        v_scores = [_norm(min_d, max_d, d) for d in distances]
    else:
        v_scores = [1.0] * len(documents)

    rerank_enabled = bool(getattr(settings, "rag_rerank_enabled", True))
    if rerank_enabled:
        kw_scores = _bm25_scores(query, documents)
        if kw_scores:
            max_kw = max(kw_scores) if max(kw_scores) > 0 else 1.0
            kw_scores = [s / max_kw for s in kw_scores]
        else:
            kw_scores = [0.0] * len(documents)
        v_w = float(getattr(settings, "effective_vector_weight", 0.75))
        kw_w = float(getattr(settings, "effective_keyword_weight", 0.25))
        total_w = (v_w + kw_w) or 1.0
        v_w, kw_w = v_w / total_w, kw_w / total_w
    else:
        kw_scores = [0.0] * len(documents)
        v_w, kw_w = 1.0, 0.0

    out: list[tuple[str, dict, float]] = []
    for i, doc in enumerate(documents):
        combined = v_w * v_scores[i] + kw_w * kw_scores[i]
        if score_threshold is not None and distances[i] > score_threshold:
            continue
        out.append((doc, metadatas[i] if i < len(metadatas) else {}, combined))
    out.sort(key=lambda t: t[2], reverse=True)
    return [(d, m) for d, m, _ in out]


def _compute_ranks(scores: list[float]) -> list[int]:
    """Compute ranks from scores (1 = best). Ties get same rank."""
    if not scores:
        return []
    # Create list of (score, original_index)
    indexed = [(s, i) for i, s in enumerate(scores)]
    # Sort by score descending
    indexed.sort(key=lambda x: x[0], reverse=True)
    ranks = [0] * len(scores)
    current_rank = 1
    for i, (_, idx) in enumerate(indexed):
        if i > 0 and indexed[i][0] != indexed[i - 1][0]:
            current_rank = i + 1
        ranks[idx] = current_rank
    return ranks


# ---------------------------------------------------------------------------
# Optional Cross-Encoder Re-ranking
# ---------------------------------------------------------------------------

def _cross_encoder_rerank(
    query: str,
    candidates: list[tuple[str, dict, float]],
) -> list[tuple[str, dict, float]]:
    """Re-rank candidates using a cross-encoder model if configured.

    If no model is configured or model loading fails, falls back to
    the RRF scores (fail-open).
    """
    model_name = getattr(settings, "rag_reranking_model", "")
    if not model_name:
        return candidates

    try:
        # Lazy import to avoid dependency issues
        from sentence_transformers import CrossEncoder

        # Load model (cached in practice)
        cross_encoder = CrossEncoder(model_name)

        # Prepare query-document pairs
        pairs = [(query, doc) for doc, _, _ in candidates]

        # Get cross-encoder scores
        ce_scores = cross_encoder.predict(pairs)

        # Combine with RRF scores (simple weighted combination)
        # Cross-encoder is typically more accurate, so give it higher weight
        combined = []
        for (doc, meta, rrf_score), ce_score in zip(candidates, ce_scores):
            # Normalize CE score to [0, 1] range (sigmoid-like)
            ce_normalized = 1.0 / (1.0 + math.exp(-ce_score))
            # Weighted combination: 70% cross-encoder, 30% RRF
            final_score = 0.7 * ce_normalized + 0.3 * rrf_score
            combined.append((doc, meta, final_score))

        combined.sort(key=lambda t: t[2], reverse=True)
        return combined
    except Exception:
        # Fail-open: return original RRF-ranked candidates
        return candidates


# ---------------------------------------------------------------------------
# Keyword scoring / BM25 (sparse retrieval)
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "in", "on", "at", "to", "for", "of", "and", "or", "not", "no",
    "this", "that", "it", "as", "by", "with", "from", "so", "do", "does",
}


def _tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, drop stopwords."""
    import re

    tokens = re.findall(r"[a-zA-Z0-9_]+", text.lower())
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]


def _bm25_scores(query: str, documents: list[str]) -> list[float]:
    """Approximate BM25 scores for *query* across *documents*.

    A compact implementation (k1=1.2, b=0.75) so reranking adds value without
    pulling in a dependency.
    """
    k1 = 1.2
    b = 0.75
    tokenized_docs = [_tokenize(d) for d in documents]
    N = len(tokenized_docs)
    if N == 0:
        return []

    avgdl = sum(len(d) for d in tokenized_docs) / N or 1.0
    dfs: Counter[str] = Counter()
    for tokens in tokenized_docs:
        for t in set(tokens):
            dfs[t] += 1

    idf = {}
    for t, df in dfs.items():
        idf[t] = math.log((N - df + 0.5) / (df + 0.5) + 1)

    q_tokens = _tokenize(query)
    scores: list[float] = []
    for tokens in tokenized_docs:
        tf: Counter[str] = Counter(tokens)
        dl = len(tokens) or 1
        score = 0.0
        for t in q_tokens:
            if t in tf and t in idf:
                numerator = idf[t] * tf[t] * (k1 + 1)
                denominator = tf[t] + k1 * (1 - b + b * dl / avgdl)
                score += numerator / denominator
        scores.append(score)
    return scores


def _norm(lo: float, hi: float, val: float) -> float:
    if hi - lo < 1e-9:
        return 1.0
    return 1.0 - (val - lo) / (hi - lo)


def has_documents() -> bool:
    """Return ``True`` if the collection contains at least one document."""
    collection = get_collection()
    count = collection.count()
    return count > 0