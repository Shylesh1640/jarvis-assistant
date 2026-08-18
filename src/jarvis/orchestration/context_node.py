"""Builds context for the model call from memory and retrieval."""
import logging

from jarvis.config.settings import settings
from jarvis.memory.query_quality import is_smalltalk, rewrite_retrieval_query
from jarvis.memory.retrieve import has_documents, query_context
from jarvis.orchestration.context_window import build_retrieval_query
from jarvis.orchestration.state import JarvisState

logger = logging.getLogger(__name__)

# Number of recent conversation-memory summaries injected alongside the
# retrieved document context. 0 disables memory context (legacy behaviour).
_MEMORY_SUMMARIES_IN_CONTEXT = 2


def build_context(state: JarvisState) -> JarvisState:
    """Populate ``state["retrieved_context"]`` (and ``sources``) from the RAG store.

    The retrieval query combines the user's current input with any
    highlighted ``selected_text`` so that follow-up questions about a
    snippet pull in the right surrounding context. If the vector store
    has no documents yet, retrieved_context is left empty and the rest of
    the graph still runs.

    Phase 5 quality gates (all fail-open — they never break the answer):
      * ``settings.rag_enabled`` is the master switch; when False retrieval
        is skipped entirely.
      * Small-talk / greeting messages are recognised via ``is_smalltalk``
        and skip the (wasted) embedding + search.
      * The query is rewritten to its informational core via
        ``rewrite_retrieval_query`` before embedding.
      * The relevance gate uses ``settings.effective_relevance_threshold``
        (legacy ``rag_relevance_threshold`` remains honoured).
      * Recent conversation-memory summaries are appended to the retrieved
        context (context composition), so earlier turns survive the window.

    Retrieval is best-effort: if the RAG store fails (corrupt index,
    embedding server down) the graph **degrades gracefully** — it answers
    without retrieved context and records a ``warning`` instead of failing
    the whole request.

    ``sources`` is populated with ``{"source", "chunk_id", "doc"}`` per
    hit so the UI can render citations. The formatted context block
    (``retrieved_context``) remains the model-facing string.
    """
    state.setdefault("retrieved_context", "")
    state.setdefault("sources", [])

    try:
        if not settings.rag_enabled:
            logger.debug("RAG disabled via settings — skipping context retrieval")
            return state
        if not has_documents():
            logger.debug("No documents in vector store — skipping context retrieval")
            return state

        query = build_retrieval_query(state)
        if not query:
            return state

        if is_smalltalk(query):
            logger.debug("Query classified as small-talk — skipping retrieval")
            return state

        query = rewrite_retrieval_query(query)
        if not query:
            return state

        context, sources = query_context(
            query,
            k=settings.retrieval_top_k,
            score_threshold=settings.effective_relevance_threshold or None,
            with_sources=True,
        )
        state["retrieved_context"] = context
        state["sources"] = sources
        logger.info(
            "Retrieved context (%d chars, %d sources) for query (%d chars)",
            len(context), len(sources), len(query),
        )
    except Exception as exc:  # noqa: BLE001 — graceful degradation
        logger.warning("RAG retrieval failed — answering without context: %s", exc)
        state["retrieved_context"] = ""
        state["sources"] = []
        state["warning"] = "Retrieval store unavailable — answering without retrieved context."

    _append_memory_context(state)
    return state


def _append_memory_context(state: JarvisState) -> None:
    """Append recent conversation-memory summaries to the retrieved context.

    Best-effort: DB / Chroma failures, a missing session, or no stored
    memory simply leaves the context unchanged. The memory block is capped
    so it cannot blow the context window.
    """
    if _MEMORY_SUMMARIES_IN_CONTEXT <= 0:
        return
    session_id = state.get("session_id")
    if not session_id:
        return
    try:
        from jarvis.memory.memory_store import memory_context

        block = memory_context(session_id, limit=_MEMORY_SUMMARIES_IN_CONTEXT)
        if not block:
            return
        existing = (state.get("retrieved_context") or "").strip()
        if existing:
            state["retrieved_context"] = f"{existing}\n\n{block}"
        else:
            state["retrieved_context"] = block
    except Exception as exc:  # noqa: BLE001 — memory is optional
        logger.debug("memory context append skipped: %s", exc)
