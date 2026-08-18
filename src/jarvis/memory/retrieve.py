"""Functions to query Chroma and return a context string.

Hybrid retrieval:
- *Vector* similarity (Chroma cosine) forms the base ranking.
- *Keyword* scoring (BM25-style overlap) reranks the top-K chunks so
  chunks mentioning the exact query terms get a boost on top of semantic
  similarity. The final order is a convex combination of the two scores.

Reranking happens entirely in-process (no external reranker model) so it
adds zero latency and zero extra deps. The hybrid weights come from
``settings.effective_vector_weight`` / ``settings.effective_keyword_weight``
(Phase 5 two-weight mode) with a fallback to the legacy single knob
``rerank_keyword_weight`` so existing configs are untouched. When
``settings.rag_rerank_enabled`` is False the keyword layer is skipped and
ranking is pure vector similarity.
"""
from __future__ import annotations

import math
from collections import Counter
from enum import Enum

from jarvis.config.settings import settings
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
        Number of top results to retrieve.
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
    emb_fn = get_embedding_function()
    col = get_collection()

    query_embedding = emb_fn.embed_query(query)

    # Over-fetch by 2x so the reranker still has candidates after filtering.
    fetch_k = max(k * 2, k)
    where = {"kind": collection.value} if collection else None

    results = col.query(
        query_embeddings=[query_embedding],
        n_results=fetch_k,
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    if not results["documents"] or not results["documents"][0]:
        return ("", []) if with_sources else ""

    documents = results["documents"][0]
    distances = (results.get("distances") or [[]])[0]
    metadatas = (results.get("metadatas") or [[]])[0]

    gathered: list[str] = []
    sources: list[dict[str, str]] = []
    reranked = _rerank(query, documents, distances, metadatas, score_threshold)

    # Per-source cap: keep at most `settings.retrieval_per_source_limit`
    # chunks per distinct source so one large document can't crowd out the
    # rest (0 = unlimited, legacy behaviour).
    per_source = max(0, int(getattr(settings, "retrieval_per_source_limit", 0)))
    seen_sources: Counter[str] = Counter()
    # Dedup by (source, chunk_id) so a chunk surfaced twice (e.g. via the
    # overlap splitter) is only injected once.
    seen_chunks: set[tuple[str, str]] = set()

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


# ---------------------------------------------------------------------------
# Keyword scoring / reranking
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


def _rerank(
    query: str,
    documents: list[str],
    distances: list[float],
    metadatas: list[dict],
    score_threshold: float | None,
) -> list[tuple[str, dict]]:
    """Combine vector distance + BM25 keyword score to produce a reranked list.

    Vector "score" = 1 - distance (cosine → similarity). Keyword score is the
    BM25 value. We min-max-normalize each score to [0,1] and combine via
    ``settings.effective_vector_weight`` / ``settings.effective_keyword_weight``
    (Phase 5 two-weight mode). When ``settings.rag_rerank_enabled`` is False
    the keyword layer is skipped entirely (pure vector ranking), and the
    legacy ``rerank_keyword_weight`` knob keeps working for existing configs.
    """
    if not documents:
        return []

    # Normalize vector distance → vector similarity score.
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


def _norm(lo: float, hi: float, val: float) -> float:
    if hi - lo < 1e-9:
        return 1.0
    return 1.0 - (val - lo) / (hi - lo)


def has_documents() -> bool:
    """Return ``True`` if the collection contains at least one document."""
    collection = get_collection()
    count = collection.count()
    return count > 0
