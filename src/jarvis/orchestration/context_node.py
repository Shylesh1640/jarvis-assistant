"""Builds context for the model call from memory and retrieval."""
from jarvis.memory.retrieve import has_documents, query_context
from jarvis.orchestration.state import JarvisState


def build_context(state: JarvisState) -> JarvisState:
    if has_documents():
        context = query_context(state["user_input"], k=4)
        state["retrieved_context"] = context
    else:
        state["retrieved_context"] = ""
    return state
