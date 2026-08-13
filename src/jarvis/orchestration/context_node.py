"""Builds context for the model call from memory and retrieval."""
import logging

from jarvis.config.settings import settings
from jarvis.memory.retrieve import has_documents, query_context
from jarvis.orchestration.context_window import build_retrieval_query
from jarvis.orchestration.state import JarvisState

logger = logging.getLogger(__name__)


def build_context(state: JarvisState) -> JarvisState:
    """Populate ``state["retrieved_context"]`` (and ``sources``) from the RAG store.

    The retrieval query combines the user's current input with any
    highlighted ``selected_text`` so that follow-up questions about a
    snippet pull in the right surrounding context. If the vector store
    has no documents yet, retrieved_context is left empty and the rest of
    the graph still runs.

    ``sources`` is populated with ``{"source", "chunk_id", "doc"}`` per
    hit so the UI can render citations. The formatted context block
    (``retrieved_context``) remains the model-facing string.
    """
    if not has_documents():
        state["retrieved_context"] = ""
        state["sources"] = []
        logger.debug("No documents in vector store — skipping context retrieval")
        return state

    query = build_retrieval_query(state)
    if not query:
        state["retrieved_context"] = ""
        state["sources"] = []
        return state

    context, sources = query_context(
        query,
        k=settings.retrieval_top_k,
        score_threshold=settings.rag_relevance_threshold or None,
        with_sources=True,
    )
    state["retrieved_context"] = context
    state["sources"] = sources
    logger.info(
        "Retrieved context (%d chars, %d sources) for query (%d chars)",
        len(context), len(sources), len(query),
    )
    return state
