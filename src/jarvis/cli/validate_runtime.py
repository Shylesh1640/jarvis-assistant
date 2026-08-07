"""Startup / runtime validator.

Usage::

    uv run jarvis-validate-runtime

Checks (best-effort, never fatal):
  * Ollama is reachable
  * The configured general model exists (via /api/show)
  * The model can answer a short test request (ping)
  * GPU diagnostics are available
  * The runtime settings are valid

Exits 0 when the core checks pass; exits non-zero only when Ollama is
unreachable OR the configured model is missing. GPU diagnostics being
unavailable is a WARNING, not a failure (so the app still runs on a
machine without nvidia-smi).
"""
from __future__ import annotations

import sys

import httpx

from jarvis.config.settings import settings, validate_runtime_settings
from jarvis.models.runtime_diagnostics import (
    check_ollama_reachable,
    get_gpu_info,
    get_ollama_version,
)


def _ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def main() -> int:
    print("Jarvis runtime validation")
    print("=" * 40)
    failures = 0

    base_url = settings.ollama_base_url.rstrip("/")

    reachable, reach_warnings = check_ollama_reachable()
    if reachable:
        version = get_ollama_version() or "unknown"
        _ok(f"Ollama reachable at {base_url} (version {version})")
    else:
        _fail(f"Ollama unreachable at {base_url}")
        for w in reach_warnings:
            _warn(w)
        failures += 1

    model_ok = False
    if reachable:
        try:
            r = httpx.post(
                f"{base_url}/api/show",
                json={"model": settings.general_model},
                timeout=10,
            )
            if r.status_code == 200:
                _ok(f"Configured general model exists: {settings.general_model}")
                model_ok = True
            elif r.status_code == 404:
                _fail(f"Model not found: {settings.general_model}. Run `ollama list` and check .env GENERAL_MODEL.")
                failures += 1
            else:
                _warn(f"/api/show returned HTTP {r.status_code}; cannot confirm model exists.")
        except Exception as exc:  # noqa: BLE001
            _warn(f"Could not verify model existence: {exc.__class__.__name__}")

    if reachable and model_ok:
        try:
            r = httpx.post(
                f"{base_url}/api/chat",
                json={
                    "model": settings.general_model,
                    "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
                    "stream": False,
                    "options": {"num_predict": 5},
                },
                timeout=60,
            )
            if r.status_code == 200:
                _ok("Test chat request succeeded.")
            else:
                _warn(f"Test chat returned HTTP {r.status_code}; model may be loading or unavailable.")
        except Exception as exc:  # noqa: BLE001
            _warn(f"Test chat request failed: {exc.__class__.__name__}")

    gpu, gpu_warnings = get_gpu_info()
    if gpu is not None:
        _ok(f"GPU detected: {gpu['gpu_name']} ({gpu['vram_used_mb']}/{gpu['vram_total_mb']} MB)")
    else:
        for w in gpu_warnings:
            _warn(w)
        _warn("GPU diagnostics unavailable. The app will still run; processor split will be 'Unknown'.")

    warns = validate_runtime_settings()
    if not warns:
        _ok("Runtime settings are valid.")
    else:
        for w in warns:
            _warn(w)

    print("=" * 40)
    if failures:
        print(f"Validation FAILED ({failures} hard error(s)).")
        return 1
    print("Validation PASSED (warnings above are informational).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
