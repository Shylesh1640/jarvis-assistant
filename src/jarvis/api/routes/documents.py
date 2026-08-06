"""Routes for the RAG document store.

* ``GET /documents/count`` — chunk count in the vector store.
* ``POST /documents/upload`` — multipart upload of .txt/.md files that
  are chunked and upserted into Chroma (the HTTP equivalent of
  ``jarvis ingest``).
* ``POST /documents/ingest-folder`` — trigger a folder scan + ingest for
  clients that already have files on disk (e.g. mounted volumes).
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from langchain_core.documents import Document

from jarvis.config.settings import settings
from jarvis.memory.store import get_collection, ingest_documents

logger = logging.getLogger("jarvis.api.documents")

router = APIRouter(prefix="/documents", tags=["documents"])

_ALLOWED_UPLOAD_EXTS = {"txt", "md", "markdown", "rst"}
_MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB per file


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
    """Chunk + upsert uploaded .txt/.md files into the RAG store.

    Returns the number of files ingested and the chunk ids assigned. Ids
    are deterministic (per file path + chunk content), so re-uploading
    the same file is a no-op.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    documents: list[Document] = []
    accepted: list[str] = []
    for f in files:
        name, data = _check_upload(f)
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=422, detail=f"{name} is not valid UTF-8") from exc
        documents.append(Document(page_content=content, metadata={"source": name}))
        accepted.append(name)

    try:
        ids = ingest_documents(documents)
    except Exception as exc:  # noqa: BLE001
        logger.exception("ingest failed")
        raise HTTPException(status_code=500, detail=f"Ingest failed: {exc}") from exc

    logger.info("Uploaded %d file(s) -> %d chunk(s)", len(accepted), len(ids))
    return {"files": accepted, "chunks": len(ids), "ids": ids}


@router.post("/ingest-folder")
def ingest_folder(folder: str | None = None) -> dict:
    """Scan a folder on disk and ingest supported files into the store."""
    target = Path(folder or settings.docs_folder)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Folder not found: {target}")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {target}")

    documents: list[Document] = []
    for path in sorted(target.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower().lstrip(".") not in _ALLOWED_UPLOAD_EXTS:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            logger.warning("Skipping %s (not UTF-8)", path)
            continue
        documents.append(Document(page_content=content, metadata={"source": str(path)}))

    if not documents:
        return {"files": 0, "chunks": 0, "ids": []}

    ids = ingest_documents(documents)
    logger.info("Ingested folder %s -> %d chunk(s)", target, len(ids))
    return {"files": len(documents), "chunks": len(ids), "ids": ids}
