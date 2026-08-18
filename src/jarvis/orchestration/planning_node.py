"""Planning node for complex, multi-step requests.

Phase 5 :: Planning. When a request is routed to the ``complex`` intent and
``settings.max_plan_steps > 0``, this node produces a short step plan before
the complex branch runs. The plan is injected into the context window (so
the model answers with a clear structure) and capped to ``max_plan_steps``
so a runaway prompt cannot balloon into an unbounded plan.

The node is best-effort: any failure (Ollama down, malformed reply, router
disabled) degrades to ``state["plan"] = []`` and the request proceeds as if
planning never ran.
"""
from __future__ import annotations

import logging

from jarvis.config.settings import settings
from jarvis.models.ollama_client import get_router_model
from jarvis.orchestration.state import JarvisState

logger = logging.getLogger(__name__)

_PLAN_PROMPT_TEMPLATE = (
    "You are a planning assistant. Break the user's request into a short, "
    "ordered list of concrete steps. Constraints:\n"
    "- At most {max_steps} steps.\n"
    '- Reply with ONLY a JSON array of strings, e.g. ["step one", "step two"].\n'
    "- Each step must be actionable and self-contained.\n\n"
    "User request:\n{text}"
)

_PLAN_OPEN = "<<<TASK PLAN>>>"
_PLAN_CLOSE = "<<<END TASK PLAN>>>"


def _plan_prompt(text: str, max_steps: int) -> str:
    return _PLAN_PROMPT_TEMPLATE.replace("{max_steps}", str(max_steps)).replace("{text}", text)


def _parse_plan(raw: str) -> list[str]:
    """Parse the planner's JSON array reply; [] on any malformed result."""
    import json

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    steps = [str(s).strip() for s in data if str(s).strip()]
    return steps


def _build_plan(text: str, max_steps: int) -> list[str]:
    """Ask the local router model for a plan; fail-open on any error."""
    if max_steps <= 0:
        return []
    try:
        llm = get_router_model()
        resp = llm.invoke(_plan_prompt(text, max_steps))
        raw = getattr(resp, "content", "") or ""
        steps = _parse_plan(raw)
    except Exception as exc:  # noqa: BLE001 — planning is optional
        logger.warning("Plan generation failed (%s); continuing without a plan", exc)
        return []
    return steps[:max_steps]


def plan_task(state: JarvisState) -> JarvisState:
    """Generate a capped step plan for complex requests.

    Only runs when ``settings.max_plan_steps > 0`` and the intent is
    ``complex``. Populates ``state["plan"]`` (list of step strings) and a
    pre-rendered ``state["plan_block"]`` used by the context window.
    """
    state.setdefault("plan", [])
    state.setdefault("plan_block", "")

    max_steps = max(0, settings.max_plan_steps)
    if max_steps <= 0 or state.get("intent") != "complex":
        return state

    steps = _build_plan(state.get("user_input", ""), max_steps)
    state["plan"] = steps
    if steps:
        rendered = [_PLAN_OPEN]
        for i, step in enumerate(steps, 1):
            rendered.append(f"{i}. {step}")
        rendered.append(_PLAN_CLOSE)
        state["plan_block"] = "\n".join(rendered)
        logger.info("Plan generated for complex request: %d step(s)", len(steps))
    return state


def format_plan_block(plan_block: str) -> str:
    """Return the plan block as a standalone context section (or "")."""
    cleaned = (plan_block or "").strip()
    return cleaned


__all__ = ["plan_task", "format_plan_block", "_PLAN_OPEN", "_PLAN_CLOSE"]