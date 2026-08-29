"""LangGraph definition wiring together the orchestration nodes."""
import logging

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from jarvis.orchestration.approval_node import approval_gate, check_risk
from jarvis.orchestration.branches import (
    run_coding_branch,
    run_complex_branch,
    run_general_branch,
)
from jarvis.orchestration.context_node import build_context
from jarvis.orchestration.deep_think import deep_think
from jarvis.orchestration.planning_node import plan_task
from jarvis.orchestration.router_node import classify_intent
from jarvis.orchestration.state import JarvisState
from jarvis.tools.registry import all_tools

logger = logging.getLogger(__name__)


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
        return "execute_tools"
    logger.info("No tool calls — ending graph")
    return END


def route_after_tools(state: JarvisState) -> str:
    """Route back to the branch that requested the tool so its loop continues."""
    intent = state.get("intent", "general")
    if intent == "coding":
        return "coding_llm"
    return "general_llm"


def _clip_content(content: object, limit: int = 4000) -> str:
    text = content if isinstance(content, str) else str(content)
    if len(text) > limit:
        text = text[:limit] + "\n... (truncated)"
    return text


def record_tools(state: JarvisState) -> JarvisState:
    """Capture names/results/errors of the tool calls just executed.

    Scans only the ToolMessages appended after the most recent AIMessage
    that requested tools, so repeated visits to this node never double
    count an earlier round. Populates ``tools_used``, ``tool_results`` and
    ``tool_errors`` (a ToolMessage whose content starts with "Error" is
    treated as an execution failure).
    """
    msgs = state.get("messages", [])
    anchor = 0
    for i, m in enumerate(msgs):
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            anchor = i

    seen: set[str] = set(state.get("tools_used", []))
    tools_used: list[str] = list(state.get("tools_used", []))
    results: list[dict] = list(state.get("tool_results", []))
    errors: list[dict] = list(state.get("tool_errors", []))

    for m in msgs[anchor + 1:]:
        if not isinstance(m, ToolMessage):
            continue
        name = getattr(m, "name", None) or ""
        content = _clip_content(getattr(m, "content", None))
        if name and name not in seen:
            seen.add(name)
            tools_used.append(name)
        entry = {"name": name, "content": content}
        if content.lstrip().startswith("Error"):
            errors.append(entry)
        else:
            results.append(entry)

    state["tools_used"] = tools_used
    state["tool_results"] = results
    state["tool_errors"] = errors
    if tools_used:
        logger.info("Tools used so far: %s", tools_used)
    if errors:
        logger.warning("Tool errors so far: %s", [e["name"] for e in errors])
    return state


def build_graph():
    graph = StateGraph(JarvisState)

    graph.add_node("classify_intent", classify_intent)
    graph.add_node("plan_task", plan_task)
    graph.add_node("build_context", build_context)
    graph.add_node("deep_think", deep_think)
    graph.add_node("general_llm", run_general_branch)
    graph.add_node("coding_llm", run_coding_branch)
    graph.add_node("check_risk", check_risk)
    graph.add_node("approval_gate", approval_gate)
    graph.add_node("execute_tools", ToolNode(all_tools()))
    graph.add_node("record_tools", record_tools)
    graph.add_node("complex_branch", run_complex_branch)

    graph.set_entry_point("classify_intent")
    graph.add_edge("classify_intent", "plan_task")
    graph.add_edge("plan_task", "build_context")
    graph.add_edge("build_context", "deep_think")

    graph.add_conditional_edges(
        "deep_think",
        route_decision,
        {
            "general": "general_llm",
            "coding": "coding_llm",
            "complex": "complex_branch",
        },
    )

    graph.add_edge("general_llm", "check_risk")
    graph.add_edge("coding_llm", "check_risk")
    graph.add_edge("complex_branch", END)

    graph.add_conditional_edges(
        "check_risk",
        route_after_risk,
        {
            "approval_gate": "approval_gate",
            "execute_tools": "execute_tools",
            END: END,
        },
    )
    graph.add_edge("approval_gate", END)
    graph.add_edge("execute_tools", "record_tools")
    graph.add_conditional_edges("record_tools", route_after_tools, {
        "coding_llm": "coding_llm",
        "general_llm": "general_llm",
    })

    logger.info("Graph built with approval nodes + tool loops + checkpointer + deep_think")
    return graph.compile(checkpointer=InMemorySaver())


jarvis_graph = build_graph()