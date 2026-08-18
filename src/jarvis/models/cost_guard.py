"""Performance / cost guardrails for the cloud (OpenRouter) path.

Phase 7 :: performance & cost guardrails. Two cheap, explicit guards so the
cloud chain cannot burn unbounded money:

* ``CLOUD_MAX_PROMPT_TOKENS`` — an estimated prompt-token ceiling. Requests
  whose prompt exceeds it are refused and fall back to the local branch
  instead of paying for a huge cloud prompt.
* ``CLOUD_DAILY_BUDGET_USD`` — a rough daily spend budget. Spend is estimated
  per call (prompt tokens x a $/1M-token table) and accumulated in-process;
  once the budget is crossed, cloud calls are refused until the budget
  window resets.

Both guards are fail-closed for *cloud* calls only: when triggered, the
complex branch already falls back to the local general model, so a refused
cloud call never breaks the answer. ``0`` disables each guard (legacy).
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

from jarvis.config.settings import settings
from jarvis.orchestration.context_window import estimate_tokens

# Rough $ per 1M prompt tokens, keyed by a model-name substring. Used only
# for the daily-budget estimate — not an invoice. Unknown models fall back
# to a conservative $3.00/1M.
_PRICE_PER_1M_TOKENS: dict[str, float] = {
    "claude": 15.00,
    "gpt-5.5": 1.25,
    "gpt": 1.25,
    "gemini": 2.50,
    "openai": 1.25,
    "deepseek": 0.55,
}


class CloudBudgetExceededError(RuntimeError):
    """Daily cloud-spend budget reached — cloud calls are paused."""


class CloudPromptTooLargeError(RuntimeError):
    """Estimated prompt exceeds ``cloud_max_prompt_tokens``."""


def _price_per_1m(model: str) -> float:
    lower = (model or "").lower()
    for key, price in _PRICE_PER_1M_TOKENS.items():
        if key in lower:
            return price
    return 3.00


def estimate_prompt_cost_usd(model: str, messages: list[dict]) -> float:
    """Estimate the USD cost of a single cloud call from its prompt size."""
    tokens = estimate_tokens(" ".join(str(m.get("content", "")) for m in messages))
    return tokens / 1_000_000 * _price_per_1m(model)


class CostGuard:
    """In-process daily cloud-spend guard + prompt-size gate. Thread-safe."""

    def __init__(
        self,
        *,
        max_prompt_tokens: int | None = None,
        daily_budget_usd: float | None = None,
    ) -> None:
        self.max_prompt_tokens = (
            settings.cloud_max_prompt_tokens
            if max_prompt_tokens is None
            else max_prompt_tokens
        )
        self.daily_budget_usd = (
            settings.cloud_daily_budget_usd
            if daily_budget_usd is None
            else daily_budget_usd
        )
        self._lock = threading.Lock()
        self._day = self._today()
        self._spent_today = 0.0
        # History of estimated costs for diagnostics: [(timestamp, model, usd)].
        self._calls: list[tuple[str, str, float]] = []

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _rollover(self) -> None:
        today = self._today()
        if today != self._day:
            self._day = today
            self._spent_today = 0.0

    def check_prompt(self, messages: list[dict], model: str) -> None:
        """Raise if the prompt exceeds the token cap (guard 1)."""
        if self.max_prompt_tokens <= 0:
            return
        tokens = estimate_tokens(" ".join(str(m.get("content", "")) for m in messages))
        if tokens > self.max_prompt_tokens:
            raise CloudPromptTooLargeError(
                f"Cloud prompt estimated at {tokens} tokens, exceeding "
                f"CLOUD_MAX_PROMPT_TOKENS={self.max_prompt_tokens}. Falling "
                f"back to local models."
            )

    def check_budget(self) -> None:
        """Raise if the daily budget is exhausted (guard 2)."""
        if self.daily_budget_usd <= 0:
            return
        with self._lock:
            self._rollover()
            if self._spent_today >= self.daily_budget_usd:
                raise CloudBudgetExceededError(
                    f"Cloud daily budget (${self.daily_budget_usd:.2f}) "
                    f"reached. Falling back to local models."
                )

    def record_call(self, model: str, messages: list[dict]) -> float:
        """Accumulate an estimated cost for a completed cloud call."""
        cost = estimate_prompt_cost_usd(model, messages)
        with self._lock:
            self._rollover()
            self._spent_today += cost
            self._calls.append((self._today(), model, cost))
        return cost

    def spend_today(self) -> float:
        with self._lock:
            self._rollover()
            return self._spent_today

    def stats(self) -> dict:
        with self._lock:
            self._rollover()
            return {
                "day": self._day,
                "spent_today_usd": round(self._spent_today, 6),
                "daily_budget_usd": self.daily_budget_usd,
                "max_prompt_tokens": self.max_prompt_tokens,
                "calls_today": sum(1 for c in self._calls if c[0] == self._day),
                "recent_calls": [
                    {"day": d, "model": m, "cost_usd": round(c, 6)}
                    for d, m, c in self._calls[-20:]
                ],
            }


_guard = CostGuard()


def get_cost_guard() -> CostGuard:
    return _guard


def reload_cost_guard() -> CostGuard:
    """Rebuild the guard from current settings (used by tests/startup)."""
    global _guard
    _guard = CostGuard()
    return _guard


__all__ = [
    "CostGuard",
    "CloudBudgetExceededError",
    "CloudPromptTooLargeError",
    "get_cost_guard",
    "reload_cost_guard",
    "estimate_prompt_cost_usd",
]