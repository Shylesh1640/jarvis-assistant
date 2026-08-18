"""GET /cost — cloud cost-guard diagnostics.

Phase 7 :: performance & cost guardrails. Exposes the accumulated estimated
cloud spend so an operator can see whether the daily budget is approaching.
Never exposes OPENROUTER_API_KEY or other secrets.
"""
from __future__ import annotations

from fastapi import APIRouter

from jarvis.models.cost_guard import get_cost_guard

router = APIRouter(prefix="/cost", tags=["cost"])


@router.get("")
def cost() -> dict:
    """Return the cloud cost-guard snapshot.

    Shape: ``{"day", "spent_today_usd", "daily_budget_usd",
    "max_prompt_tokens", "calls_today", "recent_calls"}``. Estimates are
    prompt-only and approximate — a guardrail, not an invoice.
    """
    return get_cost_guard().stats()