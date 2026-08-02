"""CLI: ingest .txt/.md files from a folder into the Jarvis RAG store.

Usage::

    # ingest everything under data/docs (the default folder from settings)
    uv run python -m jarvis.cli.ingest

    # ingest a specific folder
    uv run python -m jarvis.cli.ingest --folder path/to/notes

    # limit to a list of extensions (default: .txt, .md)
    uv run python -m jarvis.cli.ingest --ext txt --ext md

The script reads each file, wraps it as a LangChain ``Document`` with
``metadata["source"]`` set to the file path, and calls
``ingest_documents`` from ``jarvis.memory.store``. Existing chunks are
upserted by deterministic ID, so this is safe to run repeatedly.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from langchain_core.documents import Document

from jarvis.config.settings import settings
from jarvis.memory.store import ingest_documents

logger = logging.getLogger("jarvis.ingest")


def _iter_files(folder: Path, extensions: list[str]) -> list[Path]:
    ext_lower = {e.lower().lstrip(".") for e in extensions}
    files: list[Path] = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower().lstrip(".") in ext_lower:
            files.append(path)
    return files


def _load_documents(files: list[Path]) -> list[Document]:
    docs: list[Document] = []
    for path in files:
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            logger.warning("Skipping %s (not valid UTF-8)", path)
            continue
        except OSError as exc:
            logger.warning("Skipping %s (%s)", path, exc)
            continue
        if not content.strip():
            continue
        docs.append(Document(page_content=content, metadata={"source": str(path)}))
    return docs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jarvis.ingest",
        description="Ingest .txt/.md files into the Jarvis RAG vector store.",
    )
    parser.add_argument(
        "--folder",
        default=settings.docs_folder,
        help=f"Folder to scan (default: {settings.docs_folder})",
    )
    parser.add_argument(
        "--ext",
        action="append",
        default=None,
        help="File extension to include (repeatable). Default: txt and md.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the files that would be ingested, but do not write to the store.",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable debug logging."
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    folder = Path(args.folder)
    if not folder.exists():
        logger.error("Folder does not exist: %s", folder)
        return 2
    if not folder.is_dir():
        logger.error("Path is not a directory: %s", folder)
        return 2

    extensions = args.ext or ["txt", "md"]
    files = _iter_files(folder, extensions)
    logger.info("Found %d file(s) under %s (exts=%s)", len(files), folder, extensions)
    if not files:
        return 0

    if args.dry_run:
        for f in files:
            logger.info("[dry-run] would ingest %s (%d bytes)", f, f.stat().st_size)
        return 0

    docs = _load_documents(files)
    if not docs:
        logger.warning("No readable documents found.")
        return 0

    logger.info("Ingesting %d document(s) into %s ...", len(docs), settings.vector_db_path)
    ids = ingest_documents(docs)
    logger.info("Done. Stored %d chunk(s) (across %d documents).", len(ids), len(docs))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
