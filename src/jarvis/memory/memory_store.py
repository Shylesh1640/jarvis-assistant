"""Conversation memory controls + retrieval.

Phase 5 :: Conversation memory quality.

The summarizer writes ``SummaryRow`` rows to the DB and mirrors each
summary into Chroma with ``source = "session:<id>"`` (kind ``memory``).
This module owns the *controls* around that memory:

* ``list_session_memory`` — view the stored summaries for a session.
* ``delete_memory`` — remove a single summary (DB row + its Chroma chunk).
* ``clear_session_memory`` — wipe all memory for a session (DB + Chroma).
* ``export_session_memory`` — render the memory as Markdown for download.
* ``memory_context`` — build a compact "conversation memory" block that
  ``build_context`` injects alongside retrieved document context.

Chroma chunk ids for summaries are derived deterministically from
``(session_id, summary)`` so a single summary can be removed from the
vector store without a full re-ingest.

Everything is best-effort: DB or Chroma failures are logged and swallowed
so the chat path keeps working.
"""
from __future__ import annotations

import logging
import uuid


from jarvis.config.settings import settings
from jarvis.memory.store import get_collection
from jarvis.persistence import create_all, repos

logger = logging.getLogger("jarvis.memory")

_MEMORY_KIND = "memory"


def _source_for(session_id: str) -> str:
    return f"session:{session_id}"


def _summary_chunk_id(session_id: str, summary: str) -> str:
    """Deterministic Chroma id for a single summary chunk.

    Mirrors ``ingest_documents``' id scheme
    (``uuid5(NAMESPACE_OID, f"jarvis::{source}::{chunk[:64]}")``) so the
    vector-store copy of a summary can be deleted by id. Short summaries
    are stored as one chunk, so the chunk is the whole summary text.
    """
    chunk = summary[:64]
    return uuid.uuid5(uuid.NAMESPACE_OID, f"jarvis::{_source_for(session_id)}::{chunk}").hex


def _ensure_db() -> None:
    try:
        create_all()
    except Exception as exc:  # noqa: BLE001
        logger.debug("create_all failed in memory store: %s", exc)


# ---------------------------------------------------------------------------
# Read side
# ---------------------------------------------------------------------------

def list_session_memory(session_id: str, limit: int = 50) -> list[dict]:
    """Return the stored summaries for *session_id* (newest first)."""
    _ensure_db()
    try:
        rows = repos.summaries.list_for_session(session_id, limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_session_memory failed: %s", exc)
        return []
    return [_row_to_dict(r) for r in rows]


def get_memory(summary_id: int) -> dict | None:
    _ensure_db()
    try:
        row = repos.summaries.get(summary_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_memory failed: %s", exc)
        return None
    return _row_to_dict(row) if row is not None else None


def _row_to_dict(row) -> dict:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "summary": row.summary,
        "from_message_id": row.from_message_id,
        "to_message_id": row.to_message_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# ---------------------------------------------------------------------------
# Delete / clear
# ---------------------------------------------------------------------------

def _delete_chroma_chunks(session_id: str, ids: list[str]) -> None:
    if not ids:
        return
    try:
        collection = get_collection()
        existing = collection.get(ids=ids) or {}
        present = [i for i in ids if i in (existing.get("ids") or [])]
        if present:
            collection.delete(ids=present)
    except Exception as exc:  # noqa: BLE001
        logger.warning("delete Chroma chunks failed for session %s: %s", session_id, exc)


def _delete_chroma_for_session(session_id: str) -> None:
    """Delete every Chroma chunk whose source is ``session:<id>``."""
    try:
        collection = get_collection()
        existing = collection.get(where={"source": _source_for(session_id)}) or {}
        ids = existing.get("ids") or []
        if ids:
            collection.delete(ids=ids)
    except Exception as exc:  # noqa: BLE001
        logger.warning("delete Chroma session chunks failed: %s", exc)


def delete_memory(summary_id: int) -> bool:
    """Delete one summary (DB row + its Chroma chunk). Returns True on success."""
    _ensure_db()
    try:
        row = repos.summaries.get(summary_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("delete_memory lookup failed: %s", exc)
        return False
    if row is None:
        return False
    try:
        repos.summaries.delete(summary_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("delete_memory row failed: %s", exc)
        return False
    _delete_chroma_chunks(row.session_id, [_summary_chunk_id(row.session_id, row.summary)])
    logger.info("Deleted memory summary %s for session %s", summary_id, row.session_id)
    return True


def clear_session_memory(session_id: str) -> int:
    """Delete all summaries for *session_id*; returns the number removed."""
    _ensure_db()
    try:
        n = repos.summaries.delete_all_for_session(session_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("clear_session_memory rows failed: %s", exc)
        return 0
    _delete_chroma_for_session(session_id)
    if n:
        logger.info("Cleared %d memory summary(ies) for session %s", n, session_id)
    return n


def export_session_memory(session_id: str) -> str:
    """Render the session's memory as Markdown for download / display."""
    items = list_session_memory(session_id)
    if not items:
        return "# Conversation memory\n\n*(nothing stored yet)*\n"
    lines = ["# Conversation memory", ""]
    for it in items:
        created = (it.get("created_at") or "")[:19].replace("T", " ")
        lines.append(f"## Summary #{it['id']}  ({created})")
        lines.append("")
        lines.append(it["summary"])
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Context composition
# ---------------------------------------------------------------------------

def memory_context(session_id: str, limit: int = 2, max_chars: int = 1200) -> str:
    """Build a compact "conversation memory" block from recent summaries.

    Returns "" when nothing is stored. The block is capped to ``max_chars``
    so a long memory cannot blow the context window.
    """
    items = list_session_memory(session_id, limit=limit)
    if not items:
        return ""
    block = ["<<<CONVERSATION MEMORY>>>"]
    for it in items:
        block.append(f"[memory #{it['id']}] {it['summary']}")
    block.append("<<<END MEMORY>>>")
    text = "\n".join(block)
    if max_chars > 0 and len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "\n…[truncated]"
    return text


# ---------------------------------------------------------------------------
# Storage helper (shared with summaries.py)
# ---------------------------------------------------------------------------

def store_summary(
    session_id: str,
    summary: str,
    *,
    from_message_id: int | None = None,
    to_message_id: int | None = None,
) -> int | None:
    """Persist a summary to the DB and mirror it into Chroma.

    Returns the new SummaryRow id, or None on failure. The Chroma copy is
    written with a deterministic chunk id so ``delete_memory`` can remove
    it later.
    """
    try:
        summary_id = repos.summaries.add(
            session_id,
            summary=summary,
            from_message_id=from_message_id,
            to_message_id=to_message_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("store summary in DB failed: %s", exc)
        return None

    try:
        # Store as a single deterministic chunk so delete_memory can target
        # it by id (one chunk per summary).
        collection = get_collection()
        chunk_id = _summary_chunk_id(session_id, summary)
        from jarvis.memory.store import get_embedding_function

        embedding = get_embedding_function().embed_documents([summary])
        collection.upsert(
            ids=[chunk_id],
            documents=[summary],
            embeddings=embedding,
            metadatas=[{
                "source": _source_for(session_id),
                "kind": _MEMORY_KIND,
                "chunk_id": chunk_id,
            }],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("ingest summary into Chroma failed: %s", exc)
    return summary_id


__all__ = [
    "list_session_memory",
    "get_memory",
    "delete_memory",
    "clear_session_memory",
    "export_session_memory",
    "memory_context",
    "store_summary",
    "memory_context_cap",
]


def memory_context_cap() -> int:
    """Cap (chars) for the conversation-memory context block."""
    return settings.rag_context_token_cap * 4 if settings.rag_context_token_cap > 0 else 2400