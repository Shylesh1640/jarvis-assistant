"""LangGraph definition wiring together the orchestration nodes."""
import logging

from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from jarvis.orchestration.approval_node import approval_gate, check_risk
from jarvis.orchestration.branches import (
    run_coding_branch,
    run_complex_branch,
    run_general_branch,
)
from jarvis.orchestration.context_node import build_context
from jarvis.orchestration.router_node import classify_intent
from jarvis.orchestration.state import JarvisState
from jarvis.tools.coding.file_ops import read_file
from jarvis.tools.general.calculator import calculator
from jarvis.tools.general.rag_search import rag_search

logger = logging.getLogger(__name__)

_GENERAL_TOOLS = [calculator, rag_search]
_CODING_TOOLS = [read_file]


def route_decision(state: JarvisState) -> str:
    intent = state.get("intent", "general")
    logger.info("Routing decision: %s", intent)
    return intent


def route_after_risk(state: JarvisState) -> str:
    if state.get("approval_required"):
        logger.warning("Approval required: %s", state.get("pending_action"))
        return "approval_gate"
    messages = state.get("messages", [])
    if messages and hasattr(messages[-1], "tool_calls") and messages[-1].tool_calls:
        logger.info("Routing to tools after risk check")
        return "general_tools"
    logger.info("No tool calls — ending graph")
    return END


def build_graph():
    graph = StateGraph(JarvisState)

    graph.add_node("classify_intent", classify_intent)
    graph.add_node("build_context", build_context)
    graph.add_node("general_llm", run_general_branch)
    graph.add_node("check_risk", check_risk)
    graph.add_node("approval_gate", approval_gate)
    graph.add_node("general_tools", ToolNode(_GENERAL_TOOLS))
    graph.add_node("coding_branch", run_coding_branch)
    graph.add_node("complex_branch", run_complex_branch)

    graph.set_entry_point("classify_intent")
    graph.add_edge("classify_intent", "build_context")

    graph.add_conditional_edges(
        "build_context",
        route_decision,
        {
            "general": "general_llm",
            "coding": "coding_branch",
            "complex": "complex_branch",
        },
    )

    graph.add_edge("general_llm", "check_risk")
    graph.add_conditional_edges(
        "check_risk",
        route_after_risk,
        {
            "approval_gate": "approval_gate",
            "general_tools": "general_tools",
            END: END,
        },
    )
    graph.add_edge("approval_gate", END)
    graph.add_edge("general_tools", "general_llm")

    graph.add_edge("coding_branch", END)
    graph.add_edge("complex_branch", END)

    logger.info("Graph built with approval nodes")
    return graph.compile()


jarvis_graph = build_graph()
