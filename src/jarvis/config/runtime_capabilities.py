"""Runtime-mode resolution and capability detection.

The assistant supports two runtime backends:

* **local** (default) — SQLite persistence, embedded Chroma store, in-process
  task executor. No Docker services are required or contacted.
* **docker** — optional Postgres persistence + containerized deployment. Adds
  an optional Postgres dependency; everything else stays local/embedded.

``auto`` prefers local and only selects Docker when the services it would use
(Postgres) are actually configured and reachable.

This module resolves the effective mode and builds the capabilities object
that is exposed on ``GET /runtime``. Pure logic, no subprocess calls.
"""
from __future__ import annotations

from typing import Any

from jarvis.config.settings import settings

RUNTIME_MODES = ("local", "docker", "auto")
_VALID_MODES = ("local", "docker")


def resolve_runtime_mode(mode: str | None = None, *, docker_reachable: bool = False) -> str:
    """Resolve the effective runtime mode.

    ``auto`` prefers local and only selects Docker when the Postgres backend
    it would use is configured (``POSTGRES_DSN`` set) *and* reachable. Any
    unknown value falls back to local.
    """
    m = (mode or settings.runtime_mode or "local").strip().lower()
    if m == "auto":
        if settings.postgres_dsn and docker_reachable:
            return "docker"
        return "local"
    return m if m in _VALID_MODES else "local"


def get_runtime_capabilities(*, docker_reachable: bool = False) -> dict[str, Any]:
    """Return the capabilities object for the current runtime configuration.

    Fields:

    * ``runtime_mode`` — resolved effective mode (local|docker)
    * ``database_backend`` — sqlite | postgresql (effective, per POSTGRES_DSN)
    * ``vector_store_backend`` — always ``chroma_embedded`` (PersistentClient)
    * ``task_backend`` — always ``in_process`` (ThreadPoolExecutor)
    * ``docker_required`` — true only in resolved docker mode
    * ``docker_detected`` — whether a Docker daemon is reachable
    * ``warnings`` — actionable notes about the configuration
    """
    mode = resolve_runtime_mode(docker_reachable=docker_reachable)
    warnings: list[str] = []

    if mode == "docker":
        docker_required = True
        if not settings.postgres_dsn:
            warnings.append(
                "RUNTIME_MODE=docker expects POSTGRES_DSN; with it empty the app "
                "falls back to SQLite (still works without Docker)."
            )
        if not docker_reachable:
            warnings.append(
                "RUNTIME_MODE=docker but the Docker daemon is not reachable — the "
                "compose services are unavailable."
            )
    else:
        docker_required = False
        if settings.postgres_dsn:
            warnings.append(
                "RUNTIME_MODE=local but POSTGRES_DSN is set — the app still uses "
                "Postgres; for a pure local deployment clear POSTGRES_DSN."
            )
        if docker_reachable:
            warnings.append(
                "A Docker daemon is running but RUNTIME_MODE=local does not require it."
            )

    return {
        "runtime_mode": mode,
        "database_backend": "postgresql" if settings.postgres_dsn else "sqlite",
        "vector_store_backend": "chroma_embedded",
        "task_backend": "in_process",
        "docker_required": docker_required,
        "docker_detected": docker_reachable,
        "warnings": warnings,
    }