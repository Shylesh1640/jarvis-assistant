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
# Multi-format document extraction (PDF, DOCX, TXT, MD)
# ---------------------------------------------------------------------------

def extract_text_from_file(path) -> tuple[str, list[dict]]:
    """Extract text + page-section metadata from a file on disk.

    Returns ``(text, sections)`` where each section is
    ``{"page": int, "section": str, "text": str}``.

    Supports: PDF, DOCX, TXT, MD, RST. For other formats, attempts UTF-8
    read. Never crashes on extraction failure — returns an empty result.
    """
    from pathlib import Path

    p = Path(path)
    ext = p.suffix.lower().lstrip(".")
    sections: list[dict] = []
    text = ""

    if ext == "pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(p))
            pages = []
            for i, page in enumerate(reader.pages, 1):
                pg_text = (page.extract_text() or "").strip()
                if pg_text:
                    pages.append(pg_text)
                    sections.append({"page": i, "section": f"page-{i}", "text": pg_text})
            text = "\n\n".join(pages)
        except Exception as exc:  # noqa: BLE001
            import logging

            logging.getLogger("jarvis.store").warning("PDF extraction failed for %s: %s", p, exc)
            return "", []

    elif ext == "docx":
        try:
            import docx2txt

            text = (docx2txt.process(str(p)) or "").strip()
            sections.append({"page": 1, "section": "docx-body", "text": text})
        except Exception as exc:  # noqa: BLE001
            import logging

            logging.getLogger("jarvis.store").warning("DOCX extraction failed for %s: %s", p, exc)
            return "", []

    else:
        # TXT / MD / RST / unknown — plain UTF-8 read.
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
            sections.append({"page": 1, "section": "file", "text": text})
        except OSError as exc:
            import logging

            logging.getLogger("jarvis.store").warning("File read failed for %s: %s", p, exc)
            return "", []

    return text, sections


def ingest_file(path, *, metadata: dict | None = None) -> list[str]:
    """Extract text from a file (PDF/DOCX/TXT/MD), chunk, and ingest.

    Enriches each chunk's metadata with:
      - ``source``: the filename
      - ``page``: page number (for PDFs) or 1
      - ``section``: section label
      - ``filename``: original filename
      - ``timestamp``: ISO-format ingest time

    Returns the list of Chroma chunk IDs.
    """
    from datetime import datetime, timezone
    from pathlib import Path

    from langchain_core.documents import Document

    p = Path(path)
    text, sections = extract_text_from_file(p)
    if not text:
        return []

    base_meta = dict(metadata or {})
    base_meta.setdefault("source", p.name)
    base_meta.setdefault("filename", p.name)
    base_meta.setdefault("timestamp", datetime.now(timezone.utc).isoformat())

    docs: list[Document] = []
    for sec in sections:
        chunks = _split_text(sec["text"])
        for chunk in chunks:
            cm = {**base_meta, "page": sec.get("page", 1), "section": sec.get("section", "")}
            docs.append(Document(page_content=chunk, metadata=cm))

    return ingest_documents(docs) if docs else []


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

    # When a single explicit ``doc_id`` is supplied but the text splits
    # into multiple chunks, every chunk must still get a *unique* id
    # (Chroma rejects duplicate ids on ``add``). We namespace the supplied
    # id per chunk and echo that id back in the chunk's metadata so the
    # caller can still correlate them.
    explicit_id = doc_id
    for i, chunk in enumerate(chunks):
        if explicit_id is not None and len(chunks) > 1:
            chunk_id = f"{explicit_id}#{i}"
        else:
            chunk_id = explicit_id or uuid.uuid4().hex
        ids.append(chunk_id)
        documents.append(chunk)
        metadatas.append({**base_meta, "chunk_id": chunk_id, "chunk_index": i})

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
        chunk_id = (
            ids[i]
            if ids is not None
            else uuid.uuid5(uuid.NAMESPACE_OID, f"jarvis::{i}::{text[:64]}").hex
        )
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

    A ``kind`` metadata tag (one of: docs / memory / code / conversations)
    may be present in the document's metadata; if missing it defaults to
    ``"docs"``. This lets ``query_context`` restrict to a logical collection
    via a Chroma ``where`` filter without managing multiple physical stores.
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
        kind = (doc.metadata or {}).get("kind", "docs") or "docs"
        for chunk in _split_text(doc.page_content):
            chunk_id = uuid.uuid5(
                uuid.NAMESPACE_OID, f"jarvis::{source}::{chunk[:64]}"
            ).hex
            ids.append(chunk_id)
            documents.append(chunk)
            metas = {**(doc.metadata or {}), "chunk_id": chunk_id, "kind": kind}
            metadatas.append(metas)

    embeddings = emb_fn.embed_documents(documents)
    collection.upsert(
        ids=ids,
        documents=documents,
        embeddings=embeddings,
        metadatas=metadatas,
    )
    return ids
