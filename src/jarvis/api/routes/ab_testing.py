"""A/B testing API routes (Phase 13).

Exposes test creation, traffic assignment, metric recording, reporting and
promotion. All responses are JSON and never contain secrets (they are
redacted). Errors use :class:`jarvis.api.errors.APIError`.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request

from jarvis.ab_testing.manager import (
    ABTestConfig,
    ABTestManager,
    MetricEvent,
    VARIANT_A,
    VARIANT_B,
    redact,
)
from jarvis.api.errors import APIError
from jarvis.config.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ab-testing", tags=["ab-testing"])


def _manager() -> ABTestManager:
    return ABTestManager()


def _enabled() -> None:
    if not settings.ab_testing_reasoning_enabled:
        raise APIError(503, "disabled", "A/B testing for reasoning is disabled.")


@router.get("/active")
def list_active_tests() -> dict:
    _enabled()
    tests = [c.to_dict() for c in _manager().list_active_tests()]
    return redact({"active_tests": tests})


@router.get("/assignment/{session_id}")
def get_assignment(session_id: str, name: str, task_type: str | None = None) -> dict:
    _enabled()
    if not name:
        raise APIError(422, "invalid_name", "Query parameter 'name' is required.")
    try:
        variant = _manager().assign_variant(name, session_id, task_type=task_type)
    except ValueError as exc:
        raise APIError(404, "not_found", str(exc)) from exc
    return redact({"name": name, "session_id": session_id, "variant": variant})


@router.post("/record-metric")
async def record_metric(request: Request) -> dict:
    _enabled()
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise APIError(422, "invalid_json", f"Body must be JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise APIError(422, "invalid_payload", "Body must be a JSON object.")
    name = payload.get("name")
    session_id = payload.get("session_id")
    variant = payload.get("variant")
    if not name or not session_id or not variant:
        raise APIError(422, "missing_fields", "name, session_id and variant are required.")
    try:
        event = MetricEvent(
            name=str(name),
            session_id=str(session_id),
            variant=str(variant),
            task_type=payload.get("task_type"),
            accuracy=_opt_float(payload.get("accuracy")),
            relevance=_opt_float(payload.get("relevance")),
            satisfaction=_opt_float(payload.get("satisfaction")),
            tokens=_opt_int(payload.get("tokens")),
            latency_ms=_opt_float(payload.get("latency_ms")),
            feedback=payload.get("feedback"),
        )
        _manager().record_metric(event)
    except ValueError as exc:
        raise APIError(422, "invalid_variant", str(exc)) from exc
    return redact({"recorded": True, "name": name, "variant": variant})


@router.get("/report/{name}")
def get_report(name: str) -> dict:
    _enabled()
    mgr = _manager()
    if mgr.get_config(name) is None:
        raise APIError(404, "not_found", f"Test '{name}' not found.")
    report = mgr.analyze(name)
    return redact(report.to_dict())


@router.post("/create-reasoning")
async def create_reasoning(request: Request) -> dict:
    if not settings.ab_testing_reasoning_enabled:
        raise APIError(503, "disabled", "A/B testing for reasoning is disabled.")
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise APIError(422, "invalid_json", f"Body must be JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise APIError(422, "invalid_payload", "Body must be a JSON object.")
    name = payload.get("name")
    variant_a = payload.get("variant_a")
    variant_b = payload.get("variant_b")
    if not name or not variant_a or not variant_b:
        raise APIError(422, "missing_fields", "name, variant_a and variant_b are required.")
    metrics = _opt_str_list(payload.get("success_metrics"))
    ttypes = _opt_str_list(payload.get("task_types"))
    try:
        cfg = _manager().create_reasoning_test(
            str(name),
            str(variant_a),
            str(variant_b),
            traffic_split=_opt_float(payload.get("traffic_split"), default=50.0),
            success_metrics=metrics,
            task_types=ttypes,
            stratify_by_task_type=bool(payload.get("stratify_by_task_type", False)),
        )
    except ValueError as exc:
        raise APIError(409, "create_failed", str(exc)) from exc
    return redact(cfg.to_dict())


@router.post("/promote")
async def promote(request: Request) -> dict:
    _enabled()
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise APIError(422, "invalid_json", f"Body must be JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise APIError(422, "invalid_payload", "Body must be a JSON object.")
    name = payload.get("name")
    variant = payload.get("variant")
    if not name or variant not in (VARIANT_A, VARIANT_B):
        raise APIError(422, "missing_fields", "name and variant ('A' or 'B') are required.")
    try:
        cfg = _manager().promote(str(name), str(variant))
    except ValueError as exc:
        raise APIError(404, "not_found", str(exc)) from exc
    return redact(cfg.to_dict())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _opt_float(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _opt_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _opt_str_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()] or None
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return None


__all__ = ["router"]
