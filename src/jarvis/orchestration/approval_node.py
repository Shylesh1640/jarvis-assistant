"""Risk-check and approval-gate nodes for human-in-the-loop."""
import logging

from langchain_core.messages import AIMessage

from jarvis.guardrails.risk import check_tool_risk
from jarvis.orchestration.state import JarvisState

logger = logging.getLogger(__name__)


def check_risk(state: JarvisState) -> JarvisState:
    """Examine the most recent LLM message for tool calls and set risk level.

    If the risk is ``"medium"`` or ``"high"`` and the user has **not**
    already approved, ``approval_required`` is set to ``True`` and
    ``pending_action`` is populated with a human-readable description.
    """
    messages = state.get("messages", [])
    if not messages:
        state["risk_level"] = "low"
        state["approval_required"] = False
        state["pending_action"] = None
        return state

    last = messages[-1]
    if not isinstance(last, AIMessage) or not last.tool_calls:
        state["risk_level"] = "low"
        state["approval_required"] = False
        state["pending_action"] = None
        return state

    # --- user has already approved → let everything through ---
    if state.get("approved"):
        logger.info("User approved — allowing tool execution")
        state["risk_level"] = "low"
        state["approval_required"] = False
        state["approved"] = False  # Reset so subsequent LLM calls proceed normally
        return state

    # --- classify every tool call ---
    max_risk: str = "low"
    descriptions: list[str] = []
    for tc in last.tool_calls:
        risk = check_tool_risk(tc.get("name", ""), tc.get("args", {}))
        if risk == "high":
            max_risk = "high"
        elif risk == "medium" and max_risk != "high":
            max_risk = "medium"

        # Build a readable summary for the UI
        args_str = ", ".join(
            f"{k}={v!r}" for k, v in tc.get("args", {}).items()
        )
        descriptions.append(f"{tc['name']}({args_str})")

    state["risk_level"] = max_risk
    state["approval_required"] = max_risk in ("medium", "high")
    state["pending_action"] = "; ".join(descriptions) if descriptions else None

    if state["approval_required"]:
        logger.warning("Tool risk=%s — approval required: %s", max_risk, state["pending_action"])
    else:
        logger.info("Tool risk=%s — no approval needed", max_risk)

    return state


def approval_gate(state: JarvisState) -> JarvisState:
    """Called when approval is required but has not yet been given.

    Sets ``final_response`` to a message asking the user for permission.
    The graph will reach ``END`` after this node, and the caller (API
    layer) is responsible for storing the pending tool calls and
    re-invoking with ``approved=True`` when the user consents.
    """
    action_desc = state.get("pending_action", "perform an action")
    state["final_response"] = (
        f"I'd like to {action_desc}.\n"
        "Shall I proceed? (Reply with approval to continue.)"
    )
    logger.info("Approval gate triggered: %s", action_desc)
    return state
