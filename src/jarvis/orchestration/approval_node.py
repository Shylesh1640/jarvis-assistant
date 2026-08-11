"""Risk-check and approval-gate nodes for human-in-the-loop."""
import logging
import uuid
from datetime import datetime, timedelta, timezone

from langchain_core.messages import AIMessage

from jarvis.config.settings import settings
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

    Records the exact pending tool calls (name + args) so a later approval
    can execute exactly those stored calls, stamps an approval id + expiry
    (TTL), and sets ``final_response`` to a message asking the user for
    permission. The graph reaches ``END`` after this node; the caller (API
    layer) stores the state and re-invokes with ``approved=True`` when the
    user consents before the expiry.
    """
    action_desc = state.get("pending_action", "perform an action")
    messages = state.get("messages", [])
    pending: list[dict] = []
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.tool_calls:
            pending = [
                {"name": tc.get("name", ""), "args": tc.get("args", {}) or {}}
                for tc in msg.tool_calls
            ]
            break

    state["pending_tool_calls"] = pending
    state["approval_id"] = uuid.uuid4().hex
    ttl = max(60, getattr(settings, "approval_ttl_seconds", 600))
    state["approval_expires_at"] = (
        datetime.now(timezone.utc) + timedelta(seconds=ttl)
    ).isoformat()

    state["final_response"] = (
        f"I'd like to {action_desc}.\n"
        f"Approval '{(state['approval_id'])[:8]}…' expires in {ttl}s. "
        "Reply with approval to continue."
    )
    logger.info(
        "Approval gate triggered: %s (id=%s, ttl=%ds)",
        action_desc, state["approval_id"], ttl,
    )
    return state


def approval_is_expired(state: JarvisState) -> bool:
    """True when a stored approval's expiry has passed (ISO-8601 UTC).

    Missing/malformed expiry is treated as not-yet-expired so a legacy
    pending approval still follows its normal path.
    """
    raw = state.get("approval_expires_at")
    if not raw:
        return False
    try:
        expires_at = datetime.fromisoformat(raw)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return datetime.now(timezone.utc) > expires_at
