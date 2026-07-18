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
