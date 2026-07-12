"""Client for the complex/cloud path via OpenRouter, with model fallback."""
from __future__ import annotations

import httpx

from jarvis.config.settings import settings


def _extract_content(payload: dict) -> str:
    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError("OpenRouter response did not include any choices")

    message = choices[0].get("message") or {}
    content = message.get("content")
    if not content:
        raise RuntimeError("OpenRouter response did not include message content")

    return content


def get_openrouter_model(model_name: str, temperature: float = 0.4) -> tuple[str, float]:
    return model_name, temperature


def run_complex_with_fallback(messages: list[dict]) -> tuple[str, str]:
    """Try each model in the complex chain until one succeeds.

    Returns (response_text, model_used).
    """
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")

    last_error: Exception | None = None
    for model_name in settings.complex_models:
        try:
            _, temperature = get_openrouter_model(model_name)
            response = httpx.post(
                f"{settings.openrouter_base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.openrouter_api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "http://localhost",
                    "X-Title": "jarvis-assistant",
                },
                json={
                    "model": model_name,
                    "messages": messages,
                    "temperature": temperature,
                },
                timeout=60.0,
            )
            response.raise_for_status()
            return _extract_content(response.json()), model_name
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue
    raise RuntimeError(f"All complex models failed: {last_error}")
