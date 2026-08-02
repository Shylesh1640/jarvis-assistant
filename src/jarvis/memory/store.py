"""Chroma DB initialisation and document ingestion."""
from __future__ import annotations

import typing as t
import uuid

import chromadb
from langchain_core.documents import Document
from langchain_ollama import OllamaEmbeddings

from jarvis.config.settings import settings


# ---------------------------------------------------------------------------
# Lazy-loaded singletons
# ---------------------------------------------------------------------------

_embeddings: OllamaEmbeddings | None = None
_client: chromadb.PersistentClient | None = None
_collection: chromadb.Collection | None = None


def get_embedding_function() -> OllamaEmbeddings:
    global _embeddings
    if _embeddings is None:
        _embeddings = OllamaEmbeddings(
            model=settings.embedding_model,
            base_url=settings.ollama_base_url,
        )
    return _embeddings


def get_collection() -> chromadb.Collection:
    global _client, _collection
    if _collection is None:
        _client = chromadb.PersistentClient(path=settings.vector_db_path)
        _collection = _client.get_or_create_collection(
            name="jarvis_docs",
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


# ---------------------------------------------------------------------------
# Simple recursive text splitter  (avoids needing langchain_text_splitters)
# ---------------------------------------------------------------------------

_CHUNK_SIZE = 500
_CHUNK_OVERLAP = 50
_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]


def _split_text(text: str) -> list[str]:
    """Split *text* into chunks of roughly ``_CHUNK_SIZE`` characters."""
    if len(text) <= _CHUNK_SIZE:
        return [text]

    chunks: list[str] = []

    def _split_at(remaining: str, sep_index: int) -> None:
        if len(remaining) <= _CHUNK_SIZE:
            chunks.append(remaining)
            return

        sep = _SEPARATORS[sep_index] if sep_index < len(_SEPARATORS) else ""
        split_point = -1

        if sep:
            split_point = remaining.rfind(sep, 0, _CHUNK_SIZE)
            if split_point == -1 or split_point < _CHUNK_SIZE // 2:
                split_point = -1

        if split_point == -1 and sep_index < len(_SEPARATORS) - 1:
            _split_at(remaining, sep_index + 1)
            return

        if split_point == -1:
            split_point = _CHUNK_SIZE

        chunk = remaining[:split_point]
        chunks.append(chunk)
        overlap_start = max(0, split_point - _CHUNK_OVERLAP)
        remainder = remaining[overlap_start:]
        _split_at(remainder, sep_index)

    _split_at(text.strip(), 0)
    return chunks


# ---------------------------------------------------------------------------
# Ingestion helpers
# ---------------------------------------------------------------------------

def ingest_text(
    text: str,
    metadata: dict[str, t.Any] | None = None,
    doc_id: str | None = None,
) -> list[str]:
    """Split *text* into chunks, embed, and store in Chroma.

    Returns the list of Chroma IDs assigned to the stored chunks.
    """
    emb_fn = get_embedding_function()
    collection = get_collection()
    chunks = _split_text(text)
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, t.Any]] = []
    base_meta = dict(metadata or {})

    for chunk in chunks:
        chunk_id = doc_id or uuid.uuid4().hex
        ids.append(chunk_id)
        documents.append(chunk)
        metadatas.append({**base_meta, "chunk_id": chunk_id})

    # Chroma accepts pre-computed embeddings via the ``embeddings`` parameter.
    embeddings = emb_fn.embed_documents(documents)
    collection.add(
        ids=ids,
        documents=documents,
        embeddings=embeddings,
        metadatas=metadatas,
    )
    return ids


def ingest_texts(
    texts: list[str],
    metadatas: list[dict[str, t.Any]] | None = None,
) -> list[str]:
    """Ingest multiple texts (each as its own chunk)."""
    all_ids: list[str] = []
    for i, text in enumerate(texts):
        meta = metadatas[i] if metadatas else None
        all_ids.extend(ingest_text(text, metadata=meta))
    return all_ids


def add_texts(
    texts: list[str],
    ids: list[str] | None = None,
    metadatas: list[dict[str, t.Any]] | None = None,
) -> list[str]:
    """Add texts as individual chunks with explicit optional IDs.

    Unlike ``ingest_texts`` (which chunks each text further), this stores
    each item verbatim as a single chunk — useful when you have already
    pre-chunked the source data and want stable IDs across re-ingestions.

    If *ids* is None, deterministic UUID5 IDs are derived from the text
    content + index, so re-ingesting the same text is a no-op (Chroma
    upserts by ID).
    """
    if ids is not None and len(ids) != len(texts):
        raise ValueError("ids must have the same length as texts")
    emb_fn = get_embedding_function()
    collection = get_collection()

    resolved_ids: list[str] = []
    documents: list[str] = []
    embeddings: list[list[float]] = []
    out_metas: list[dict[str, t.Any]] = []

    for i, text in enumerate(texts):
        chunk_id = ids[i] if ids is not None else uuid.uuid5(
            uuid.NAMESPACE_OID, f"jarvis::{i}::{text[:64]}"
        ).hex
        meta = dict(metadatas[i]) if metadatas else {}
        resolved_ids.append(chunk_id)
        documents.append(text)
        out_metas.append(meta)
    embeddings = emb_fn.embed_documents(documents)

    collection.upsert(
        ids=resolved_ids,
        documents=documents,
        embeddings=embeddings,
        metadatas=out_metas,
    )
    return resolved_ids


def ingest_documents(docs: list[Document]) -> list[str]:
    """Ingest LangChain ``Document`` objects, chunking each by content.

    Each document's ``page_content`` is split into chunks via the
    module's recursive splitter and stored with the document's
    metadata (plus a generated chunk_id) so the source is attributable
    at retrieval time.
    """
    if not docs:
        return []

    emb_fn = get_embedding_function()
    collection = get_collection()

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, t.Any]] = []

    for doc in docs:
        source = (doc.metadata or {}).get("source") or "<unknown>"
        for chunk in _split_text(doc.page_content):
            chunk_id = uuid.uuid5(uuid.NAMESPACE_OID, f"jarvis::{source}::{chunk[:64]}").hex
            ids.append(chunk_id)
            documents.append(chunk)
            metadatas.append({**(doc.metadata or {}), "chunk_id": chunk_id})

    embeddings = emb_fn.embed_documents(documents)
    collection.upsert(
        ids=ids,
        documents=documents,
        embeddings=embeddings,
        metadatas=metadatas,
    )
    return ids
