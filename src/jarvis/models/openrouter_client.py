"""Client for the complex/cloud path via OpenRouter, with model fallback.

Tries each model in ``settings.complex_models`` in order. The first one
that returns a usable response wins; on total failure a ``RuntimeError``
is raised wrapping the per-model failures so the caller (the complex
branch) can fall back to the local general branch.
"""

from __future__ import annotations

import logging

import httpx

from jarvis.config.settings import settings

logger = logging.getLogger(__name__)

# Per-intent default temperature for the cloud path.
_DEFAULT_TEMPERATURE = 0.4
# How long to wait for a single model before moving to the next.
_REQUEST_TIMEOUT = 60.0


def _extract_content(payload: dict) -> str:
    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError("OpenRouter response did not include any choices")

    message = choices[0].get("message") or {}
    content = message.get("content")
    if not content:
        raise RuntimeError("OpenRouter response did not include message content")

    return content


def _post_chat(model_name: str, messages: list[dict], temperature: float) -> str:
    """POST one chat completion to OpenRouter and return the text.

    Raises on any HTTP / parsing error so the fallback loop in
    ``run_complex_with_fallback`` can try the next model.
    """
    url = f"{settings.openrouter_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost",
        "X-Title": "jarvis-assistant",
    }
    body = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
    }
    try:
        response = httpx.post(url, headers=headers, json=body, timeout=_REQUEST_TIMEOUT)
        response.raise_for_status()
        return _extract_content(response.json())
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"OpenRouter {model_name} returned HTTP {exc.response.status_code}"
        ) from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"OpenRouter {model_name} request failed: {exc}") from exc


def run_complex_with_fallback(messages: list[dict]) -> tuple[str, str]:
    """Try each model in the complex chain until one succeeds.

    Returns ``(response_text, model_used)``.
    """
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")

    if not settings.complex_models:
        raise RuntimeError("No complex models configured")

    errors: list[str] = []
    for model_name in settings.complex_models:
        try:
            text = _post_chat(model_name, messages, _DEFAULT_TEMPERATURE)
            logger.info("OpenRouter succeeded with %s", model_name)
            return text, model_name
        except Exception as exc:  # noqa: BLE001 — we want to keep trying.
            errors.append(f"{model_name}: {exc}")
            logger.warning("OpenRouter %s failed: %s", model_name, exc)
            continue

    raise RuntimeError("All complex models failed -> " + " | ".join(errors))
