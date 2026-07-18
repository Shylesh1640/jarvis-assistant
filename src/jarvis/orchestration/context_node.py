"""Builds context for the model call from memory and retrieval."""
import logging

from jarvis.memory.retrieve import has_documents, query_context
from jarvis.orchestration.state import JarvisState

logger = logging.getLogger(__name__)


def build_context(state: JarvisState) -> JarvisState:
    if has_documents():
        context = query_context(state["user_input"], k=4)
        state["retrieved_context"] = context
        logger.info("Retrieved context (%d chars)", len(context))
    else:
        state["retrieved_context"] = ""
        logger.debug("No documents in vector store — skipping context retrieval")
    return state
