"""GET /runtime — GPU + Ollama runtime diagnostics.

Exposes structured runtime/GPU information with no secrets. Safe to call
even when Ollama or nvidia-smi are unavailable: the endpoint returns
200 with a ``warnings`` list rather than erroring.
"""
from __future__ import annotations

from fastapi import APIRouter

from jarvis.models.runtime_diagnostics import get_runtime_snapshot

router = APIRouter(prefix="/runtime", tags=["runtime"])


@router.get("")
def runtime() -> dict:
    """Return the runtime/GPU diagnostics snapshot.

    Shape documented in ``runtime_diagnostics.get_runtime_snapshot``.
    Never raises; never exposes OPENROUTER_API_KEY or other secrets.
    """
    return get_runtime_snapshot()
