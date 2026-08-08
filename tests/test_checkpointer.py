"""Tests asserting the compiled graph carries a working checkpointer."""
from __future__ import annotations

from jarvis.orchestration.graph import build_graph


def test_graph_compiles_with_checkpointer():
    graph = build_graph()
    assert graph is not None
    assert graph.checkpointer is not None


def test_graph_checkpointer_is_inmemory_saver():
    from langgraph.checkpoint.memory import InMemorySaver

    graph = build_graph()
    assert isinstance(graph.checkpointer, InMemorySaver)