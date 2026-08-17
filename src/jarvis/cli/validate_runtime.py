"""Startup / runtime validator.

Usage::

    uv run jarvis-validate-runtime                 # default mode (from settings)
    uv run jarvis-validate-runtime --mode local    # local (SQLite) checks
    uv run jarvis-validate-runtime --mode docker   # Docker-backed checks

Checks (best-effort, never fatal):
  * Ollama is reachable
  * The configured general model exists (via /api/show)
  * The model can answer a short test request (ping)
  * GPU diagnostics are available
  * The runtime settings are valid

Mode-specific extras:
  * local  — SQLite storage is writable; ChromaDB store is writable; the docs
             folder exists or can be created. Docker is NOT required.
  * docker — the ``docker`` CLI exists, the daemon is reachable, the compose
             services are running, and the configured Postgres endpoint is
             reachable. Never starts/stops anything.

Exits 0 when the core checks pass; exits non-zero only when Ollama is
unreachable OR the configured model is missing. GPU diagnostics being
unavailable is a WARNING, not a failure (so the app still runs on a
machine without nvidia-smi).
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import sys
import tempfile

import httpx

from jarvis.config.runtime_capabilities import resolve_runtime_mode
from jarvis.config.settings import settings, validate_runtime_settings
from jarvis.models.runtime_diagnostics import (
    check_ollama_reachable,
    get_gpu_info,
    get_ollama_version,
)
from jarvis.models.platform_diagnostics import (
    docker_cli_available,
    docker_daemon_reachable,
    get_docker_containers,
)


def _ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def _check_local_storage() -> int:
    """Local-mode storage checks (SQLite + ChromaDB writability, docs folder)."""
    failures = 0
    try:
        with tempfile.TemporaryDirectory(prefix="jarvis-validate-") as tmp:
            probe = os.path.join(tmp, "probe.db")
            import sqlite3

            conn = sqlite3.connect(probe)
            conn.execute("CREATE TABLE t (x);")
            conn.execute("INSERT INTO t VALUES (1);")
            conn.execute("SELECT COUNT(*) FROM t;").fetchone()
            conn.close()
            _ok("SQLite storage is writable (local mode).")
    except Exception as exc:  # noqa: BLE001
        _fail(f"SQLite storage is not writable: {exc.__class__.__name__}")
        failures += 1

    try:
        tmp = tempfile.mkdtemp(prefix="jarvis-chroma-")
        try:
            import chromadb

            client = chromadb.PersistentClient(path=tmp)
            client.get_or_create_collection("validate-probe")
            _ok("Embedded ChromaDB store is writable (local mode).")
        finally:
            # Best-effort cleanup: Chroma may hold file locks on Windows, so
            # a failed removal is informational only.
            try:
                shutil.rmtree(tmp, ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        _fail(f"Embedded ChromaDB store is not writable: {exc.__class__.__name__}")
        failures += 1

    docs_folder = settings.docs_folder
    try:
        if os.path.isdir(docs_folder):
            _ok(f"Docs folder exists: {docs_folder}")
        else:
            os.makedirs(docs_folder, exist_ok=True)
            _ok(f"Docs folder created: {docs_folder}")
    except Exception as exc:  # noqa: BLE001
        _fail(f"Docs folder is not creatable: {exc.__class__.__name__}")
        failures += 1

    return failures


def _check_docker_runtime() -> int:
    """Docker-mode checks. Read-only: never starts, stops, prunes or pulls."""
    failures = 0

    if not docker_cli_available():
        _fail("`docker` CLI not found on PATH (needed for RUNTIME_MODE=docker).")
        return failures + 1

    reachable, warns = docker_daemon_reachable()
    if reachable:
        _ok("Docker daemon is reachable.")
    else:
        _fail("Docker daemon is not reachable.")
        for w in warns:
            _warn(w)
        failures += 1

    containers, _ = get_docker_containers()
    running = {c["name"] for c in containers}
    required = {"jarvis-postgres", "jarvis-backend", "jarvis-frontend"}
    if not running:
        _warn("No compose containers detected (`docker compose up -d` on the host).")
    else:
        _ok(f"Running containers: {', '.join(sorted(running))}")
    missing = required - running
    for name in sorted(missing):
        _warn(f"Compose service `{name}` is not running.")

    dsn = settings.postgres_dsn
    if not dsn:
        _fail("RUNTIME_MODE=docker expects POSTGRES_DSN; it is empty (falls back to SQLite).")
        failures += 1
    else:
        host, port = _dsn_host_port(dsn)
        if host is None:
            _warn("Could not parse host/port from POSTGRES_DSN; skipping reachability check.")
        else:
            try:
                with socket.create_connection((host, port), timeout=3):
                    _ok(f"Postgres endpoint reachable: {host}:{port}")
            except Exception as exc:  # noqa: BLE001
                _fail(f"Postgres endpoint unreachable: {host}:{port} ({exc.__class__.__name__})")
                failures += 1
    return failures


def _dsn_host_port(dsn: str) -> tuple[str | None, int]:
    """Extract (host, port) from a postgres DSN without exposing credentials."""
    try:
        from sqlalchemy.engine import make_url

        url = make_url(dsn)
        return url.host, url.port or 5432
    except Exception:  # noqa: BLE001
        return None, 5432


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="jarvis-validate-runtime",
        description="Validate the Jarvis runtime for a given mode.",
    )
    parser.add_argument(
        "--mode",
        choices=("local", "docker", "auto"),
        default=None,
        help="Runtime mode to validate for (default: resolved from settings).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    mode = resolve_runtime_mode(args.mode)

    print("Jarvis runtime validation")
    print("=" * 40)
    print(f"  Runtime mode: {mode}")
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

    if mode == "local":
        print("=" * 40)
        print("  Local-mode storage checks")
        print("=" * 40)
        failures += _check_local_storage()
    elif mode == "docker":
        print("=" * 40)
        print("  Docker-mode checks (read-only)")
        print("=" * 40)
        failures += _check_docker_runtime()

    print("=" * 40)
    if failures:
        print(f"Validation FAILED ({failures} hard error(s)).")
        return 1
    print("Validation PASSED (warnings above are informational).")
    return 0


if __name__ == "__main__":
    sys.exit(main())