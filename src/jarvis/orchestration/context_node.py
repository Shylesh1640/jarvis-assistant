"""Builds context for the model call from memory and retrieval."""
import logging

from jarvis.config.settings import settings
from jarvis.memory.retrieve import has_documents, query_context
from jarvis.orchestration.context_window import build_retrieval_query
from jarvis.orchestration.state import JarvisState

logger = logging.getLogger(__name__)


def build_context(state: JarvisState) -> JarvisState:
    """Populate ``state["retrieved_context"]`` from the RAG store.

    The retrieval query combines the user's current input with any
    highlighted ``selected_text`` so that follow-up questions about a
    snippet pull in the right surrounding context. If the vector store
    has no documents yet, retrieved_context is left empty and the rest of
    the graph still runs.
    """
    if not has_documents():
        state["retrieved_context"] = ""
        logger.debug("No documents in vector store — skipping context retrieval")
        return state

    query = build_retrieval_query(state)
    if not query:
        state["retrieved_context"] = ""
        return state

    context = query_context(query, k=settings.retrieval_top_k)
    state["retrieved_context"] = context
    logger.info(
        "Retrieved context (%d chars) for query (%d chars)",
        len(context), len(query),
    )
    return state
