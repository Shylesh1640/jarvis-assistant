"""Periodic conversation summarization.

After every ``settings.summary_every_turns`` (user, assistant) pairs in
a session, ``maybe_summarize`` asks the configured general Ollama model
to produce a short summary of the recent turns. The summary is:

* stored as a ``SummaryRow`` in the persistence layer, and
* ingested into the Chroma vector store (source = "session:<id>"),
  so subsequent turn retrieval can surface "what we discussed earlier"
  even once the sliding window has dropped those turns.

Everything is best-effort: any failure (Ollama down, DB unavailable,
embeddings failing) is logged and swallowed so the chat path keeps
working. The function returns the summary string or ``None``.
"""
from __future__ import annotations

import logging

from langchain_core.documents import Document

from jarvis.config.settings import settings
from jarvis.memory.store import ingest_documents
from jarvis.models.ollama_client import get_general_model
from jarvis.persistence import create_all, repos

logger = logging.getLogger("jarvis.summaries")

_SUMMARY_PROMPT = (
    "Summarize the following conversation in 5 short bullet points. "
    "Focus on facts, decisions, and any code or files mentioned. "
    "Do not add anything that was not said.\n\n"
)


def _recent_messages(session_id: str, limit: int) -> list[dict]:
    return repos.messages.tail(session_id, limit)


def _summarize_text(messages: list[dict]) -> str:
    if not messages:
        return ""
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
    try:
        llm = get_general_model(temperature=0.2)
        resp = llm.invoke(_SUMMARY_PROMPT + convo)
        content = getattr(resp, "content", "")
        return (content or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("summarize LLM call failed: %s", exc)
        return ""


def maybe_summarize(session_id: str) -> str | None:
    """Summarize if the turn count has crossed the threshold; else return None.

    Called from the chat route after a non-approval turn completes.
    """
    try:
        create_all()
    except Exception as exc:  # noqa: BLE001
        logger.debug("create_all failed in maybe_summarize: %s", exc)

    try:
        total = repos.messages.count_for_session(session_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("count messages failed: %s", exc)
        return None

    threshold = max(2, settings.summary_every_turns)
    # A "turn" = 2 messages (user + assistant). Summarize when we cross a
    # fresh multiple of the threshold and have not yet for this batch.
    if total == 0 or total < threshold * 2:
        return None
    last_batch = (total // (threshold * 2))
    try:
        done = repos.summaries.count_for_session(session_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("count summaries failed: %s", exc)
        return None
    if done >= last_batch:
        return None

    recent = _recent_messages(session_id, limit=threshold * 2)
    summary = _summarize_text(recent)
    if not summary:
        return None

    try:
        repos.summaries.add(
            session_id,
            summary=summary,
            from_message_id=recent[0]["id"] if recent and "id" in recent[0] else None,
            to_message_id=recent[-1]["id"] if recent and "id" in recent[-1] else None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("store summary in DB failed: %s", exc)

    try:
        ingest_documents([
            Document(
                page_content=summary,
                metadata={"source": f"session:{session_id}", "kind": "summary"},
            )
        ])
    except Exception as exc:  # noqa: BLE001
        logger.warning("ingest summary into Chroma failed: %s", exc)

    logger.info("Summarized session %s (%d messages -> %d chars)", session_id, total, len(summary))
    return summary
