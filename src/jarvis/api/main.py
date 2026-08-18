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
from jarvis.api.routes.cost import router as cost_router
from jarvis.api.routes.documents import router as documents_router
from jarvis.api.routes.feedback import router as feedback_router
from jarvis.api.routes.memory import router as memory_router
from jarvis.api.routes.runtime import router as runtime_router
from jarvis.api.routes.sessions import router as sessions_router
from jarvis.api.routes.tasks import router as tasks_router
from jarvis.api.routes.traces import router as traces_router
from jarvis.config.settings import settings
from jarvis.observability.logging_config import setup_logging

setup_logging()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(levelname)s %(name)s: %(message)s",
)


def _startup() -> None:
    """Idempotent one-time initialisation (tables, TTL sweep, task recovery)."""
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run one-time init on startup; stop the sweeper on shutdown."""
    del app
    _startup()
    from jarvis.tasks.maintenance import start_sweeper

    start_sweeper()
    yield
    from jarvis.tasks.maintenance import stop_sweeper

    stop_sweeper()


app = FastAPI(title="Jarvis Assistant API", version="0.2.0", lifespan=lifespan)


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


@app.get("/health")
def health() -> dict:
    """Basic liveness; reachability of Ollama is reported under /runtime."""
    from jarvis.models.runtime_diagnostics import check_ollama_reachable

    ollama_ok, _ = check_ollama_reachable()
    return {
        "status": "ok",
        "ollama_reachable": ollama_ok,
    }


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
