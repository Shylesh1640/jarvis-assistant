"""LangGraph definition wiring together the orchestration nodes."""
from langgraph.graph import END, StateGraph

from jarvis.orchestration.branches import (
    run_coding_branch,
    run_complex_branch,
    run_general_branch,
)
from jarvis.orchestration.context_node import build_context
from jarvis.orchestration.router_node import classify_intent
from jarvis.orchestration.state import JarvisState


def route_decision(state: JarvisState) -> str:
    return state.get("intent", "general")


def build_graph():
    graph = StateGraph(JarvisState)

    graph.add_node("classify_intent", classify_intent)
    graph.add_node("build_context", build_context)
    graph.add_node("general_branch", run_general_branch)
    graph.add_node("coding_branch", run_coding_branch)
    graph.add_node("complex_branch", run_complex_branch)

    graph.set_entry_point("classify_intent")
    graph.add_edge("classify_intent", "build_context")

    graph.add_conditional_edges(
        "build_context",
        route_decision,
        {
            "general": "general_branch",
            "coding": "coding_branch",
            "complex": "complex_branch",
        },
    )

    graph.add_edge("general_branch", END)
    graph.add_edge("coding_branch", END)
    graph.add_edge("complex_branch", END)

    return graph.compile()


jarvis_graph = build_graph()
