"""Document management for the RAG store.

Phase 5 :: Document management controls. The upload/ingest endpoints only
*added* documents; this module adds the read/delete/reindex side so an
operator can see what is indexed, inspect a single source, remove stale
documents, and rebuild the index from the configured folder.

Everything is best-effort: Chroma failures are logged and swallowed so the
read endpoints degrade to empty responses instead of 500s.
"""
from __future__ import annotations

import logging
from pathlib import Path

from jarvis.config.settings import settings
from jarvis.memory.store import get_collection, ingest_file

logger = logging.getLogger("jarvis.documents")

_PAGE_SIZE = 200


def _get_all(collection, where: dict | None = None, include=("metadatas", "documents")) -> dict:
    """Paginate ``collection.get`` to bypass Chroma's single-page limit."""
    merged = {"ids": [], "metadatas": [], "documents": []}
    offset = 0
    while True:
        batch = collection.get(
            where=where,
            include=list(include),
            limit=_PAGE_SIZE,
            offset=offset,
        ) or {}
        ids = batch.get("ids") or []
        if not ids:
            break
        merged["ids"].extend(ids)
        merged["metadatas"].extend(batch.get("metadatas") or [])
        merged["documents"].extend(batch.get("documents") or [])
        if len(ids) < _PAGE_SIZE:
            break
        offset += len(ids)
    return merged


def list_documents() -> list[dict]:
    """Return distinct sources in the store, newest-kept first.

    Each entry: ``{"source", "filename", "chunk_count", "timestamp"}``.
    """
    try:
        collection = get_collection()
        data = _get_all(collection, include=("metadatas",))
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_documents failed: %s", exc)
        return []

    docs: dict[str, dict] = {}
    for meta in data["metadatas"]:
        meta = meta or {}
        # Memory/conversation chunks are managed separately; only the
        # document corpus is listed here.
        if meta.get("kind") == "memory":
            continue
        source = meta.get("source") or meta.get("filename") or "<unknown>"
        entry = docs.setdefault(source, {"source": source, "chunk_count": 0, "timestamp": None})
        entry["chunk_count"] += 1
        ts = meta.get("timestamp")
        if ts and (entry["timestamp"] is None or ts > entry["timestamp"]):
            entry["timestamp"] = ts
        if "filename" in meta and meta["filename"] != source:
            entry["filename"] = meta["filename"]
        else:
            entry.setdefault("filename", source)

    out = sorted(docs.values(), key=lambda d: d["source"].lower())
    for e in out:
        e.setdefault("filename", e["source"])
    return out


def get_document(source: str) -> dict | None:
    """Return one source's chunks: ``{"source", "chunks": [{chunk_id, text, meta}]}``."""
    try:
        collection = get_collection()
        data = _get_all(collection, where={"source": source})
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_document failed for %s: %s", source, exc)
        return None
    ids = data.get("ids") or []
    if not ids:
        return None
    chunks = []
    for i, cid in enumerate(ids):
        metas = (data.get("metadatas") or [])[i] or {}
        chunks.append({
            "chunk_id": cid,
            "text": (data.get("documents") or [])[i],
            "page": metas.get("page"),
            "section": metas.get("section"),
            "timestamp": metas.get("timestamp"),
        })
    return {"source": source, "chunk_count": len(chunks), "chunks": chunks}


def delete_document(source: str) -> int:
    """Delete every chunk with ``source == source``; returns number removed."""
    try:
        collection = get_collection()
        existing = collection.get(where={"source": source}) or {}
        ids = existing.get("ids") or []
        if not ids:
            return 0
        collection.delete(ids=ids)
        logger.info("Deleted document %s (%d chunk(s))", source, len(ids))
        return len(ids)
    except Exception as exc:  # noqa: BLE001
        logger.warning("delete_document failed for %s: %s", source, exc)
        return 0


def clear_documents() -> int:
    """Delete every chunk with ``kind != memory`` (the document corpus)."""
    try:
        collection = get_collection()
        existing = collection.get(where={"kind": "docs"}) or {}
        ids = existing.get("ids") or []
        if not ids:
            return 0
        collection.delete(ids=ids)
        logger.info("Cleared document corpus (%d chunk(s))", len(ids))
        return len(ids)
    except Exception as exc:  # noqa: BLE001
        logger.warning("clear_documents failed: %s", exc)
        return 0


def reindex_documents(folder: str | None = None) -> dict:
    """Re-ingest the configured (or explicit) folder into the store.

    Returns ``{"files", "chunks", "skipped"}``. Extraction failures are
    counted in ``skipped`` and logged, not fatal.
    """
    target = Path(folder or settings.docs_folder)
    if not target.exists() or not target.is_dir():
        raise FileNotFoundError(f"Folder not found: {target}")

    from jarvis.api.routes.documents import _ALLOWED_UPLOAD_EXTS

    all_ids: list[str] = []
    file_count = 0
    skipped = 0
    for path in sorted(target.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower().lstrip(".") not in _ALLOWED_UPLOAD_EXTS:
            continue
        try:
            ids = ingest_file(path, metadata={"source": path.name, "filename": path.name})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping %s (extract failed): %s", path, exc)
            skipped += 1
            continue
        if ids:
            all_ids.extend(ids)
            file_count += 1
        else:
            skipped += 1

    logger.info(
        "Reindexed folder %s -> %d file(s), %d chunk(s), %d skipped",
        target, file_count, len(all_ids), skipped,
    )
    return {"files": file_count, "chunks": len(all_ids), "skipped": skipped}


__all__ = [
    "list_documents",
    "get_document",
    "delete_document",
    "clear_documents",
    "reindex_documents",
]
