"""Periodic conversation summarization.

After every ``settings.summary_every_turns`` (user, assistant) pairs in
a session, ``maybe_summarize`` asks the configured general Ollama model
to produce a short summary of the recent turns. The summary is:

* stored as a ``SummaryRow`` in the persistence layer, and
* ingested into the Chroma vector store (source = "session:<id>"),
  so subsequent turn retrieval can surface "what we discussed earlier"
  even once the sliding window has dropped those turns.

Phase 5 additions:

* **Secrets exclusion** — message content is redacted (PII / API keys /
  private paths) *before* it is sent to the summarizer LLM, so secrets
  never end up in a stored summary.
* **Evicted-turn summarization** — ``maybe_summarize_evicted`` summarizes
  the oldest turns that ``window_history`` has dropped from the sliding
  window but that have not been summarized yet, so nothing is lost.

Everything is best-effort: any failure (Ollama down, DB unavailable,
embeddings failing) is logged and swallowed so the chat path keeps
working. The functions return the summary string or ``None``.
"""
from __future__ import annotations

import logging

from jarvis.config.settings import settings
from jarvis.guardrails.output_guard import redact_output
from jarvis.memory.memory_store import store_summary
from jarvis.models.ollama_client import get_general_model
from jarvis.orchestration.context_window import window_history_from_settings
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
    # Redact PII / secrets before the content leaves the process, so they
    # can never be persisted into a summary (Phase 5 secrets exclusion).
    convo = "\n".join(
        f"{m['role']}: {redact_output(m['content'])}" for m in messages
    )
    try:
        llm = get_general_model(temperature=0.2)
        resp = llm.invoke(_SUMMARY_PROMPT + convo)
        content = getattr(resp, "content", "")
        return (content or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("summarize LLM call failed: %s", exc)
        return ""


def _store(session_id: str, summary: str, messages: list[dict]) -> None:
    store_summary(
        session_id,
        summary,
        from_message_id=messages[0].get("id") if messages and "id" in messages[0] else None,
        to_message_id=messages[-1].get("id") if messages and "id" in messages[-1] else None,
    )


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

    _store(session_id, summary, recent)
    logger.info("Summarized session %s (%d messages -> %d chars)", session_id, total, len(summary))
    return summary


def maybe_summarize_evicted(session_id: str) -> str | None:
    """Summarize turns dropped from the sliding window that aren't covered yet.

    ``window_history`` keeps the most recent ``history_max_turns`` turns
    and evicts the rest. This finds the *oldest* messages a fresh window
    would drop, and — if the newest existing summary does not already
    cover that range — summarizes them and stores the result.

    Returns the new summary string or ``None``.
    """
    try:
        create_all()
    except Exception as exc:  # noqa: BLE001
        logger.debug("create_all failed in maybe_summarize_evicted: %s", exc)

    try:
        history = repos.messages.history(session_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("load history failed: %s", exc)
        return None
    if not history:
        return None

    windowed = window_history_from_settings(history)
    evicted = history[: len(history) - len(windowed)]
    if not evicted:
        return None

    # Skip if the newest existing summary already covers the evicted range.
    try:
        latest = repos.summaries.latest_for_session(session_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("load latest summary failed: %s", exc)
        latest = None
    newest_evicted_id = evicted[-1].get("id")
    if (
        latest is not None
        and latest.to_message_id is not None
        and newest_evicted_id is not None
        and latest.to_message_id >= newest_evicted_id
    ):
        return None

    summary = _summarize_text(evicted)
    if not summary:
        return None

    _store(session_id, summary, evicted)
    logger.info(
        "Summarized %d evicted message(s) for session %s -> %d chars",
        len(evicted), session_id, len(summary),
    )
    return summary