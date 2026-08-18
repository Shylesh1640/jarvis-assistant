"""Routes for the RAG document store.

* ``GET /documents/count`` — chunk count in the vector store.
* ``POST /documents/upload`` — multipart upload of .txt/.md files that
  are chunked and upserted into Chroma (the HTTP equivalent of
  ``jarvis ingest``).
* ``POST /documents/ingest-folder`` — trigger a folder scan + ingest for
  clients that already have files on disk (e.g. mounted volumes).

Phase 5 :: document management:
* ``GET /documents`` — list distinct sources with chunk counts.
* ``GET /documents/{source}`` — chunks for one source.
* ``DELETE /documents/{source}?confirm=1`` — remove one source (destructive,
  so a confirmation flag is required).
* ``DELETE /documents?confirm=1`` — remove the whole document corpus.
* ``POST /documents/reindex`` — rebuild the index from the configured folder.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from langchain_core.documents import Document

from jarvis.api.errors import APIError
from jarvis.config.settings import settings
from jarvis.memory.document_manager import (
    clear_documents,
    delete_document,
    get_document,
    list_documents,
    reindex_documents,
)
from jarvis.memory.store import get_collection, ingest_documents, ingest_file

logger = logging.getLogger("jarvis.api.documents")

router = APIRouter(prefix="/documents", tags=["documents"])

_ALLOWED_UPLOAD_EXTS = {"txt", "md", "markdown", "rst", "pdf", "docx"}
_BINARY_EXTS = {"pdf", "docx"}
_MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB per file (PDFs can be large)


@router.get("/count")
def documents_count() -> dict[str, int]:
    """Return the number of chunks currently in the RAG vector store."""
    try:
        return {"count": int(get_collection().count())}
    except Exception as exc:  # noqa: BLE001
        logger.warning("documents_count failed: %s", exc)
        return {"count": 0}


def _check_upload(file: UploadFile) -> tuple[str, bytes]:
    name = Path(file.filename or "").name
    if not name:
        raise HTTPException(status_code=400, detail="Filename is required")
    ext = Path(name).suffix.lower().lstrip(".")
    if ext not in _ALLOWED_UPLOAD_EXTS:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type .{ext}; allowed: {sorted(_ALLOWED_UPLOAD_EXTS)}",
        )
    data = file.file.read()
    if not data:
        raise HTTPException(status_code=400, detail=f"{name} is empty")
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"{name} exceeds {_MAX_UPLOAD_BYTES} bytes",
        )
    return name, data


@router.post("/upload")
def upload_documents(files: list[UploadFile]) -> dict:
    """Chunk + upsert uploaded files into the RAG store.

    Supports TXT/MD/RST (UTF-8) and PDF/DOCX (binary extraction). Returns
    the number of files ingested and the chunk ids assigned.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    all_ids: list[str] = []
    accepted: list[str] = []
    for f in files:
        name, data = _check_upload(f)
        ext = Path(name).suffix.lower().lstrip(".")
        if ext in _BINARY_EXTS:
            # Write binary to a temp file and use ingest_file for extraction.
            suffix = f".{ext}"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(data)
                tmp_path = Path(tmp.name)
            try:
                ids = ingest_file(tmp_path, metadata={"source": name, "filename": name})
            except Exception as exc:  # noqa: BLE001
                logger.exception("extract/ingest failed for %s", name)
                raise HTTPException(status_code=500, detail=f"Failed to ingest {name}: {exc}") from exc
            finally:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            if ids:
                all_ids.extend(ids)
                accepted.append(name)
            else:
                logger.warning("No text extracted from %s", name)
        else:
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise HTTPException(status_code=422, detail=f"{name} is not valid UTF-8") from exc
            doc = Document(page_content=content, metadata={"source": name})
            try:
                ids = ingest_documents([doc])
            except Exception as exc:  # noqa: BLE001
                logger.exception("ingest failed for %s", name)
                raise HTTPException(status_code=500, detail=f"Ingest failed: {exc}") from exc
            all_ids.extend(ids)
            accepted.append(name)

    logger.info("Uploaded %d file(s) -> %d chunk(s)", len(accepted), len(all_ids))
    return {"files": accepted, "chunks": len(all_ids), "ids": all_ids}


@router.post("/ingest-folder")
def ingest_folder(folder: str | None = None) -> dict:
    """Scan a folder on disk and ingest supported files into the store.

    Uses ``ingest_file`` which handles PDF/DOCX binary extraction as well
    as plain text formats, enriching each chunk with page/section metadata.
    """
    target = Path(folder or settings.docs_folder)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Folder not found: {target}")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {target}")

    all_ids: list[str] = []
    file_count = 0
    for path in sorted(target.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower().lstrip(".") not in _ALLOWED_UPLOAD_EXTS:
            continue
        try:
            ids = ingest_file(path, metadata={"source": path.name})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping %s (extract failed): %s", path, exc)
            continue
        if ids:
            all_ids.extend(ids)
            file_count += 1

    if not all_ids:
        return {"files": 0, "chunks": 0, "ids": []}

    logger.info("Ingested folder %s -> %d file(s), %d chunk(s)", target, file_count, len(all_ids))
    return {"files": file_count, "chunks": len(all_ids), "ids": all_ids}


# ---------------------------------------------------------------------------
# Phase 5 :: document management
# ---------------------------------------------------------------------------


@router.get("")
def documents_list() -> dict:
    """List distinct sources in the store with chunk counts."""
    return {"documents": list_documents()}


@router.get("/{source}")
def documents_get(source: str) -> dict:
    """Return one source's chunks for inspection/debug."""
    doc = get_document(source)
    if doc is None:
        raise APIError(404, "document_not_found", f"No document indexed as '{source}'.")
    return doc


@router.delete("/{source}")
def documents_delete(source: str, confirm: bool = False) -> dict:
    """Remove one source from the store (requires ?confirm=1)."""
    if not confirm:
        raise APIError(
            400,
            "confirmation_required",
            "Pass ?confirm=1 to delete this document.",
        )
    removed = delete_document(source)
    if removed == 0:
        raise APIError(404, "document_not_found", f"No document indexed as '{source}'.")
    return {"deleted": source, "chunks": removed}


@router.delete("")
def documents_clear(confirm: bool = False) -> dict:
    """Remove the whole document corpus (requires ?confirm=1)."""
    if not confirm:
        raise APIError(
            400,
            "confirmation_required",
            "Pass ?confirm=1 to clear all documents.",
        )
    return {"cleared": clear_documents()}


@router.post("/reindex")
def documents_reindex(folder: str | None = None) -> dict:
    """Rebuild the index from the configured (or explicit) folder."""
    try:
        return reindex_documents(folder)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
