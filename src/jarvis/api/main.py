"""FastAPI application entrypoint."""
from fastapi import FastAPI

from jarvis.api.routes.chat import router as chat_router

app = FastAPI(title="Jarvis Assistant API")

app.include_router(chat_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
