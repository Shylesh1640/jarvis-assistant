"""Builds context for the model call from memory and retrieval (stub)."""
from jarvis.orchestration.state import JarvisState


def build_context(state: JarvisState) -> JarvisState:
    # TODO: pull from vector DB / long-term memory once memory layer is wired up.
    state["retrieved_context"] = ""
    return state
