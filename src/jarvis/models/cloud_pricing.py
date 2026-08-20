"""Cloud model pricing (Phase 6).

Loads a JSON pricing table (``CLOUD_PRICING_CONFIG_PATH``) so the cost
guardrails no longer hard-code $/1M-token figures. An exact model key wins;
otherwise substring rules are tried; otherwise a conservative default is
used. Loading is best-effort and cached — a missing or malformed file falls
back to the built-in defaults (never raises at import or on a request).

Pricing is an *estimate* for guardrails, not an invoice.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from jarvis.config.settings import settings

logger = logging.getLogger(__name__)

# Conservative built-in fallback used when the config file is unavailable.
_DEFAULT_PRICING: dict[str, Any] = {
    "default_prompt_per_1m_usd": 3.0,
    "default_completion_per_1m_usd": 9.0,
    "models": {
        "anthropic/claude-opus-4.1": {"prompt_per_1m_usd": 15.0, "completion_per_1m_usd": 75.0},
        "openai/gpt-5.5": {"prompt_per_1m_usd": 1.25, "completion_per_1m_usd": 10.0},
        "google/gemini-2.5-pro": {"prompt_per_1m_usd": 2.50, "completion_per_1m_usd": 15.0},
    },
    "substring_rules": {
        "claude": {"prompt_per_1m_usd": 15.0, "completion_per_1m_usd": 75.0},
        "gpt-5.5": {"prompt_per_1m_usd": 1.25, "completion_per_1m_usd": 10.0},
        "gpt": {"prompt_per_1m_usd": 1.25, "completion_per_1m_usd": 10.0},
        "gemini": {"prompt_per_1m_usd": 2.50, "completion_per_1m_usd": 15.0},
        "openai": {"prompt_per_1m_usd": 1.25, "completion_per_1m_usd": 10.0},
        "deepseek": {"prompt_per_1m_usd": 0.55, "completion_per_1m_usd": 2.19},
    },
}

_load_lock = threading.Lock()
_loaded: dict[str, Any] | None = None


def _merge(base: dict, override: dict) -> dict:
    """Merge *override* onto *base* (dicts recursed, exact keys kept)."""
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _read_file(path: str) -> dict | None:
    try:
        p = Path(path).expanduser()
        if not p.is_file():
            return None
        with p.open(encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return None
        return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load pricing config %s: %s", path, exc)
        return None


def load_pricing(path: str | None = None) -> dict:
    """Return the effective pricing table (cached, best-effort)."""
    global _loaded
    with _load_lock:
        if _loaded is None:
            file_data = _read_file(path or settings.cloud_pricing_config_path) or {}
            _loaded = _merge(_DEFAULT_PRICING, file_data)
        return _loaded


def reload_pricing() -> None:
    """Drop the cache (used by tests / after config edits)."""
    global _loaded
    with _load_lock:
        _loaded = None


def price_for(model: str) -> dict[str, float]:
    """Return {"prompt_per_1m_usd", "completion_per_1m_usd"} for *model*.

    Exact model key first, then the longest matching substring rule, then
    the built-in defaults.
    """
    table = load_pricing()
    exact = (table.get("models") or {}).get(model)
    if exact:
        return {
            "prompt_per_1m_usd": float(exact.get("prompt_per_1m_usd") or 0) or table.get("default_prompt_per_1m_usd") or 0,
            "completion_per_1m_usd": float(exact.get("completion_per_1m_usd") or 0) or table.get("default_completion_per_1m_usd") or 0,
        }
    lower = (model or "").lower()
    rules = table.get("substring_rules") or {}
    best: tuple[int, dict] | None = None
    for key, value in rules.items():
        if key in lower and (best is None or len(key) > best[0]):
            best = (len(key), value)
    if best is not None:
        return {
            "prompt_per_1m_usd": float(best[1].get("prompt_per_1m_usd") or 0) or table.get("default_prompt_per_1m_usd") or 0,
            "completion_per_1m_usd": float(best[1].get("completion_per_1m_usd") or 0) or table.get("default_completion_per_1m_usd") or 0,
        }
    return {
        "prompt_per_1m_usd": float(table.get("default_prompt_per_1m_usd") or 3.0),
        "completion_per_1m_usd": float(table.get("default_completion_per_1m_usd") or 9.0),
    }


def estimate_cost_usd(
    model: str,
    prompt_tokens: int,
    completion_tokens: int = 0,
) -> float:
    """Estimate USD for *prompt_tokens* + *completion_tokens* of *model*."""
    price = price_for(model)
    return (
        prompt_tokens / 1_000_000 * price["prompt_per_1m_usd"]
        + completion_tokens / 1_000_000 * price["completion_per_1m_usd"]
    )


DEFAULT_PRICING: dict[str, Any] = _DEFAULT_PRICING

__all__ = [
    "DEFAULT_PRICING",
    "estimate_cost_usd",
    "load_pricing",
    "price_for",
    "reload_pricing",
]