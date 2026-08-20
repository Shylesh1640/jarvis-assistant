"""FastAPI application entrypoint."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from jarvis.api.errors import (
    APIError,
    api_error_to_json,
    build_error_body,
    unexpected_error_to_json,
)
from jarvis.api.routes.chat import router as chat_router
from jarvis.api.routes.calendar import router as calendar_router
from jarvis.api.routes.connectors import router as connectors_router
from jarvis.api.routes.email_drafts import router as email_drafts_router
from jarvis.api.routes.ide import router as ide_router
from jarvis.api.routes.voice import router as voice_router
from jarvis.api.routes.cost import router as cost_router
from jarvis.api.routes.documents import router as documents_router
from jarvis.api.routes.feedback import router as feedback_router
from jarvis.api.routes.memory import router as memory_router
from jarvis.api.routes.runtime import router as runtime_router
from jarvis.api.routes.sessions import router as sessions_router
from jarvis.api.routes.tasks import router as tasks_router
from jarvis.api.routes.traces import router as traces_router
from jarvis.api.routes.todos import router as todos_router
from jarvis.api.security import install_security_stack
from jarvis.config.settings import settings
from jarvis.observability.logging_config import setup_logging

setup_logging()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(levelname)s %(name)s: %(message)s",
)


def _startup() -> None:
    """Idempotent one-time initialisation (tables, TTL sweep, task recovery)."""
    _log_deployment_warnings()
    try:
        from jarvis.persistence import create_all
        from jarvis.persistence.repo import repos

        create_all()
        repos.approvals.purge_expired()
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("jarvis.api").warning("DB init failed: %s", exc)
    try:
        from jarvis.tasks.maintenance import sweep_once

        sweep_once()
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("jarvis.api").warning("Startup maintenance sweep failed: %s", exc)
    try:
        from jarvis.tasks.runner import recover_stale_tasks

        n = recover_stale_tasks()
        if n:
            logging.getLogger("jarvis.api").info(
                "Recovered %d stale task(s) from a previous process", n
            )
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("jarvis.api").warning("Stale-task recovery failed: %s", exc)
    try:
        from jarvis.tasks.reminders import scan_once

        result = scan_once()
        if result.get("fired"):
            logging.getLogger("jarvis.api").info(
                "Startup reminder scan fired %d reminder(s)", result["fired"]
            )
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("jarvis.api").warning("Startup reminder scan failed: %s", exc)


def _log_deployment_warnings() -> None:
    """Log any deployment-profile security warnings at startup (no secrets)."""
    try:
        from jarvis.config.deployment import validate_deployment

        for warning in validate_deployment():
            logging.getLogger("jarvis.api").warning("Deployment: %s", warning)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("jarvis.api").warning(
            "Could not evaluate deployment profile: %s", exc
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run one-time init on startup; stop the sweeper on shutdown."""
    del app
    _startup()
    from jarvis.tasks.maintenance import start_sweeper

    start_sweeper()
    from jarvis.tasks.reminders import start_reminder_worker

    start_reminder_worker()
    yield
    from jarvis.tasks.maintenance import stop_sweeper

    stop_sweeper()
    from jarvis.tasks.reminders import stop_reminder_worker

    stop_reminder_worker()


app = FastAPI(title="Jarvis Assistant API", version="0.2.0", lifespan=lifespan)


# Phase 7 :: network security (CORS, trusted hosts, headers, proxy awareness).
# Installed at import so every request (including /ready) is protected.
install_security_stack(app)


@app.exception_handler(APIError)
def _api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    return api_error_to_json(exc)


@app.exception_handler(RequestValidationError)
def _validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = exc.errors()
    first = errors[0] if errors else {}
    field = ".".join(str(loc) for loc in first.get("loc", []) if loc not in ("body", "query"))
    message = first.get("msg") or "Invalid request payload."
    detail = f"{field}: {message}" if field else message
    return JSONResponse(
        status_code=422,
        content=build_error_body(
            422,
            "validation_error",
            detail,
            suggested_action="Check the request fields and retry.",
        ),
    )


@app.exception_handler(HTTPException)
def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Convert legacy plain-``detail`` errors into the structured shape."""
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    headers = dict(exc.headers or {})
    if getattr(exc, "retry_after_seconds", None) is not None:
        headers.setdefault("Retry-After", str(exc.retry_after_seconds))
    return JSONResponse(
        status_code=exc.status_code or 400,
        content=build_error_body(
            exc.status_code or 400,
            "request_failed",
            detail,
            suggested_action=None,
        ),
        headers=headers or None,
    )


@app.exception_handler(Exception)
def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logging.getLogger("jarvis.api").exception("Unhandled exception on %s", request.url.path)
    return unexpected_error_to_json(exc)


app.include_router(chat_router)
app.include_router(tasks_router)
app.include_router(documents_router)
app.include_router(memory_router)
app.include_router(feedback_router)
app.include_router(cost_router)
app.include_router(runtime_router)
app.include_router(sessions_router)
app.include_router(traces_router)
app.include_router(todos_router)
app.include_router(calendar_router)
app.include_router(email_drafts_router)
app.include_router(connectors_router)
app.include_router(ide_router)
app.include_router(voice_router)


@app.get("/health")
def health() -> dict:
    """Basic liveness; reachability of Ollama is reported under /runtime."""
    from jarvis.models.runtime_diagnostics import check_ollama_reachable

    ollama_ok, _ = check_ollama_reachable()
    return {
        "status": "ok",
        "ollama_reachable": ollama_ok,
    }


def _ready_checks() -> dict:
    """Readiness checks for the current deployment profile.

    Required checks (failing any makes the app NOT ready):
      * database — engine can run a trivial query;
      * deployment — the profile's security expectations are satisfied;
      * ollama — reachable (required for local/single_host).
    Informational checks (never fail readiness):
      * cloud — reports whether the cloud is configured and budgeted.
    Never exposes secrets.
    """
    checks: dict[str, dict] = {}

    try:
        from sqlalchemy import text

        from jarvis.persistence.engine import engine_from_settings

        with engine_from_settings().connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = {"ok": True, "detail": "database reachable", "required": True}
    except Exception as exc:  # noqa: BLE001
        checks["database"] = {
            "ok": False,
            "detail": f"database unavailable: {exc.__class__.__name__}",
            "required": True,
        }

    from jarvis.config.deployment import normalize_profile, validate_deployment

    profile = normalize_profile(settings.deployment_profile)
    warnings = validate_deployment()
    checks["deployment"] = {
        "ok": not warnings,
        "detail": "deployment profile valid" if not warnings else "; ".join(warnings),
        "required": True,
    }

    from jarvis.models.runtime_diagnostics import check_ollama_reachable

    ollama_ok, ollama_warns = check_ollama_reachable()
    required_ollama = profile in ("local", "single_host")
    checks["ollama"] = {
        "ok": ollama_ok,
        "detail": "ollama reachable" if ollama_ok else "; ".join(ollama_warns),
        "required": required_ollama,
    }

    from jarvis.config.deployment import cloud_budget_enforced

    if settings.openrouter_api_key:
        detail = (
            "configured, budget enforced"
            if cloud_budget_enforced()
            else "configured, no budget enforced"
        )
    else:
        detail = "not configured"
    checks["cloud"] = {
        "ok": True,
        "detail": detail,
        "required": False,
    }
    return checks


@app.get("/ready")
def ready():
    """Readiness probe for the selected deployment profile.

    Returns 200 with ``status: "ready"`` (or ``"degraded"`` when only
    informational checks warn) and **503** when a required dependency is
    unavailable or the profile's security expectations are not met. Never
    exposes secrets.
    """
    from fastapi.responses import JSONResponse

    checks = _ready_checks()
    required_ok = all(c["ok"] for c in checks.values() if c.get("required"))
    informational_ok = all(
        c["ok"] for c in checks.values() if not c.get("required")
    )
    if required_ok and informational_ok:
        status = "ready"
        code = 200
    elif required_ok:
        status = "degraded"
        code = 200
    else:
        status = "not_ready"
        code = 503
    return JSONResponse(status_code=code, content={"status": status, "checks": checks})


@app.get("/models")
def models() -> dict:
    return {
        "general": {
            "provider": "ollama",
            "model": settings.general_model,
            "base_url": settings.ollama_base_url,
        },
        "coding": {
            "provider": "ollama",
            "model": settings.coding_model,
            "model_small": settings.coding_model_small,
            "base_url": settings.ollama_base_url,
        },
        "strong_local": {
            "provider": "ollama",
            "model": settings.strong_local_model,
            "base_url": settings.ollama_base_url,
        },
        "complex": {
            "provider": "openrouter",
            "models": settings.complex_models,
            "base_url": settings.openrouter_base_url,
            "configured": bool(settings.openrouter_api_key),
        },
    }
