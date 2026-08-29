"""Performance analysis API routes (Phase 13)."""
from __future__ import annotations

import logging

from fastapi import APIRouter

from jarvis.api.errors import APIError
from jarvis.config.settings import settings
from jarvis.performance import analysis as perf

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/performance", tags=["performance"])


def _enabled() -> bool:
    if not getattr(settings, "performance_analysis_enabled", True):
        raise APIError(503, "disabled", "Performance analysis is disabled.")


@router.get("/summary")
def get_summary(days: int | None = None) -> dict:
    _enabled()
    report = perf.generate_report(days=days)
    return redact_report(report.to_dict())


@router.get("/by-strategy")
def get_by_strategy(strategy: str | None = None, days: int | None = None) -> dict:
    _enabled()
    return redact_report(perf.analyze_by_strategy(strategy=strategy, days=days))


@router.get("/by-task-type")
def get_by_task_type(task_type: str | None = None, days: int | None = None) -> dict:
    _enabled()
    return redact_report(perf.analyze_by_task_type(task_type=task_type, days=days))


@router.get("/trends")
def get_trends(days: int | None = None) -> dict:
    _enabled()
    report = perf.generate_report(days=days)
    return redact_report(report.to_dict())


def redact_report(value: dict) -> dict:
    return perf.redact(value)


__all__ = ["router"]
