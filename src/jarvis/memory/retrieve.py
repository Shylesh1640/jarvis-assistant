"""Functions to query Chroma and return a context string."""
from __future__ import annotations

from jarvis.memory.store import get_collection, get_embedding_function


def query_context(
    query: str,
    k: int = 4,
    score_threshold: float | None = None,
) -> str:
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

    Returns
    -------
    A string suitable for injecting into an LLM prompt as retrieved context,
    or an empty string if nothing relevant is found.
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
        return ""

    documents = results["documents"][0]
    distances = (results.get("distances") or [None])[0]
    metadatas = (results.get("metadatas") or [None])[0]

    gathered: list[str] = []
    for i, doc in enumerate(documents):
        dist = distances[i] if distances else None
        meta = metadatas[i] if metadatas else {}
        if score_threshold is not None and dist is not None:
            if dist > score_threshold:
                continue

        tag = f"[Doc {i + 1}]"
        if src := meta.get("source"):
            tag = f"[{src}]"
        gathered.append(f"{tag} {doc}")

    if not gathered:
        return ""

    return "\n\n".join(gathered)


def has_documents() -> bool:
    """Return ``True`` if the collection contains at least one document."""
    collection = get_collection()
    count = collection.count()
    return count > 0
