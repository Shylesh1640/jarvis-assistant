"""FastAPI application entrypoint."""
import logging

from fastapi import FastAPI
from jarvis.config.settings import settings

from jarvis.api.routes.chat import router as chat_router

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(levelname)s %(name)s: %(message)s",
)

app = FastAPI(title="Jarvis Assistant API")

app.include_router(chat_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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
