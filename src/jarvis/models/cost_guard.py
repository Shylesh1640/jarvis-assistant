"""Performance / cost guardrails for the cloud (OpenRouter) path.

Phase 7 :: performance & cost guardrails, hardened in Phase 6 with
persistent spend records and per-request / per-session budgets:

* ``CLOUD_MAX_PROMPT_TOKENS`` — an estimated prompt-token ceiling. Requests
  whose prompt exceeds it are refused and fall back to the local branch
  instead of paying for a huge cloud prompt.
* ``CLOUD_DAILY_BUDGET_USD`` — a rough daily spend budget (legacy, in-process).
* ``CLOUD_MAX_REQUEST_COST_USD`` — cap on the estimated cost of a single call.
* ``CLOUD_MAX_SESSION_COST_USD`` — cap on a session's total cloud spend
  (backed by the persistent ``cloud_usage`` table).
* ``CLOUD_REQUIRE_COST_APPROVAL`` — the complex branch pauses for explicit
  approval before spending (handled in ``orchestration/branches.py``).

Estimates come from ``config/model_pricing.json`` (see ``cloud_pricing``) and
use the response's real token usage when available. Prices are an estimate —
a guardrail, not an invoice. All guards are fail-closed for *cloud* calls
only: when triggered, the complex branch falls back to the local general
model, so a refused cloud call never breaks the answer. ``0`` disables each
individual guard (legacy).
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from jarvis.config.settings import settings
from jarvis.models.cloud_pricing import estimate_cost_usd
from jarvis.orchestration.context_window import estimate_tokens

logger = logging.getLogger(__name__)


class CloudBudgetExceededError(RuntimeError):
    """Daily cloud-spend budget reached — cloud calls are paused."""


class CloudPromptTooLargeError(RuntimeError):
    """Estimated prompt exceeds ``cloud_max_prompt_tokens``."""


class CloudRequestCostExceededError(RuntimeError):
    """Estimated cost of a single call exceeds ``cloud_max_request_cost_usd``."""


class CloudSessionBudgetExceededError(RuntimeError):
    """Session cloud-spend cap (``cloud_max_session_cost_usd``) reached."""


def estimate_prompt_cost_usd(model: str, messages: list[dict]) -> float:
    """Estimate the USD cost of a single cloud call from its prompt size."""
    tokens = estimate_tokens(" ".join(str(m.get("content", "")) for m in messages))
    return estimate_cost_usd(model, tokens)


class CostGuard:
    """Cloud spend guards + persistent usage records. Thread-safe."""

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

    def check_request_cost(self, model: str, estimated_usd: float) -> None:
        """Raise if the estimated single-call cost exceeds the cap."""
        cap = settings.cloud_max_request_cost_usd
        if cap <= 0:
            return
        if estimated_usd > cap:
            raise CloudRequestCostExceededError(
                f"Cloud call to {model} estimated at ${estimated_usd:.4f}, "
                f"exceeding CLOUD_MAX_REQUEST_COST_USD=${cap:.2f}. "
                f"Falling back to local models."
            )

    def check_session_cost(self, session_id: str | None, estimated_usd: float) -> None:
        """Raise if *session_id*'s total cloud spend would exceed its cap.

        Backed by the persistent ``cloud_usage`` table; best-effort when the
        DB is unavailable (the in-process daily budget stays fail-closed).
        """
        cap = settings.cloud_max_session_cost_usd
        if cap <= 0 or not session_id:
            return
        try:
            from jarvis.persistence import repos

            spent = repos.cloud_usage.sum_for_session(session_id)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Session cloud-spend check skipped (DB unavailable) for %s",
                session_id,
            )
            return
        if spent + estimated_usd > cap:
            raise CloudSessionBudgetExceededError(
                f"Session {session_id} cloud spend would reach "
                f"${spent + estimated_usd:.2f}, exceeding "
                f"CLOUD_MAX_SESSION_COST_USD=${cap:.2f}. Falling back to local models."
            )

    def record_call(
        self,
        model: str,
        messages: list[dict],
        *,
        session_id: str | None = None,
        usage: dict | None = None,
    ) -> float:
        """Accumulate the cost of a completed cloud call and persist a record.

        Uses real token usage when *usage* = {"prompt_tokens": int,
        "completion_tokens": int} is available (more accurate), else falls
        back to a prompt-only estimate. Returns the recorded USD estimate.
        """
        usage = usage or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        if prompt_tokens or completion_tokens:
            cost = estimate_cost_usd(model, prompt_tokens, completion_tokens)
        else:
            cost = estimate_prompt_cost_usd(model, messages)
        with self._lock:
            self._rollover()
            self._spent_today += cost
            self._calls.append((self._day, model, cost))
        if settings.cloud_cost_tracking_enabled:
            self._persist(day=self._day, session_id=session_id, model=model,
                          prompt_tokens=prompt_tokens,
                          completion_tokens=completion_tokens, cost=cost)
        return cost

    @staticmethod
    def _persist(
        *,
        day: str,
        session_id: str | None,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost: float,
    ) -> None:
        try:
            from jarvis.persistence import repos

            repos.cloud_usage.add(
                day=day,
                session_id=session_id,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                estimated_cost_usd=cost,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "Cloud usage record not persisted (DB unavailable): %s", model
            )

    def spend_today(self) -> float:
        with self._lock:
            self._rollover()
            return self._spent_today

    def stats(self) -> dict:
        day = self._today()
        with self._lock:
            self._rollover()
            in_process = self._spent_today
            calls_today = sum(1 for c in self._calls if c[0] == self._day)
            recent = [
                {"day": d, "model": m, "cost_usd": round(c, 6)}
                for d, m, c in self._calls[-20:]
            ]
        persisted_spend = None
        persisted_calls = None
        if settings.cloud_cost_tracking_enabled:
            try:
                from jarvis.persistence import repos

                persisted_spend = repos.cloud_usage.sum_for_day(day)
                persisted_calls = repos.cloud_usage.count_for_day(day)
            except Exception:  # noqa: BLE001
                persisted_spend = None
                persisted_calls = None
        return {
            "day": day,
            "spent_today_usd": round(in_process, 6),
            "daily_budget_usd": self.daily_budget_usd,
            "max_prompt_tokens": self.max_prompt_tokens,
            "calls_today": calls_today,
            "recent_calls": recent,
            "request_cost_cap_usd": settings.cloud_max_request_cost_usd,
            "session_cost_cap_usd": settings.cloud_max_session_cost_usd,
            "cost_tracking_enabled": settings.cloud_cost_tracking_enabled,
            "require_cost_approval": settings.cloud_require_cost_approval,
            "persisted_today_usd": persisted_spend,
            "persisted_calls_today": persisted_calls,
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
    "CloudRequestCostExceededError",
    "CloudSessionBudgetExceededError",
    "get_cost_guard",
    "reload_cost_guard",
    "estimate_prompt_cost_usd",
]