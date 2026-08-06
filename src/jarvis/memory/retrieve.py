"""Functions to query Chroma and return a context string."""

from __future__ import annotations

from jarvis.memory.store import get_collection, get_embedding_function


def query_context(
    query: str,
    k: int = 4,
    score_threshold: float | None = None,
    *,
    with_sources: bool = False,
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

    Returns
    -------
    A string suitable for injecting into an LLM prompt as retrieved context,
    or an empty string if nothing relevant is found. With ``with_sources``
    the return is a (string, list) tuple.
    """
    emb_fn = get_embedding_function()
    collection = get_collection()

    query_embedding = emb_fn.embed_query(query)

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=k,
        include=["documents", "metadatas", "distances"],
    )

    if not results["documents"] or not results["documents"][0]:
        return ("", []) if with_sources else ""

    documents = results["documents"][0]
    distances = (results.get("distances") or [[]])[0]
    metadatas = (results.get("metadatas") or [[]])[0]

    gathered: list[str] = []
    sources: list[dict[str, str]] = []
    for i, doc in enumerate(documents):
        dist = distances[i] if i < len(distances) else None
        meta = metadatas[i] if i < len(metadatas) else {}
        if score_threshold is not None and dist is not None:
            if dist > score_threshold:
                continue

        src = meta.get("source") or f"Doc {i + 1}"
        chunk_id = meta.get("chunk_id") or ""
        tag = f"[{src}]" if src else f"[Doc {i + 1}]"
        gathered.append(f"{tag} {doc}")
        sources.append({"source": src, "chunk_id": chunk_id, "doc": doc})

    if not gathered:
        return ("", []) if with_sources else ""

    return ("\n\n".join(gathered), sources) if with_sources else "\n\n".join(gathered)


def has_documents() -> bool:
    """Return ``True`` if the collection contains at least one document."""
    collection = get_collection()
    count = collection.count()
    return count > 0
