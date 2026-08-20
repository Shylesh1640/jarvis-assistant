"""Deployment profiles (Phase 7).

Three explicit profiles drive safe defaults and validation:

* **local** — localhost-only development. SQLite + embedded ChromaDB + local
  Ollama. Session tokens optional. No public network exposure by default.
* **single_host** — one private machine/server. Docker Compose optional,
  persistent DB/vector-store locations, session tokens required, reverse
  proxy expected, no public exposure by default.
* **production** — hardened public-facing deployment: tokens required,
  backups required, strict environment validation, cloud budgets enforced,
  loopback binding prohibited unless intentional, debug mode off, detailed
  trace exposure off by default.

Validation is *fail-safe*: insecure configuration yields warnings that are
surfaced in the ``/runtime`` capability report and make ``GET /ready`` report
not-ready. The app never silently downgrades security to run.
"""
from __future__ import annotations

import socket
from typing import Any

from jarvis.config.settings import Settings, settings

DEPLOYMENT_PROFILES = ("local", "single_host", "production")

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def normalize_profile(value: str | None) -> str:
    """Normalize an arbitrary profile string to a known profile or 'local'."""
    v = (value or "").strip().lower().replace("-", "_")
    return v if v in DEPLOYMENT_PROFILES else "local"


def _host_is_loopback(host: str) -> bool:
    """True when *host* binds to a loopback/local address."""
    h = (host or "").strip().lower()
    if h in ("localhost", "127.0.0.1", "::1", "[::1]"):
        return True
    if h in ("0.0.0.0", "::", "[::]"):
        return False
    if h == "":
        return False
    try:
        return socket.getaddrinfo(h, None, socket.AF_INET)[0][4][0].startswith("127.")
    except Exception:  # noqa: BLE001
        return False


def _host_is_public(host: str) -> bool:
    """True when binding to *host* would expose the app beyond loopback."""
    return not _host_is_loopback(host)


def validate_deployment(s: Settings | None = None) -> list[str]:
    """Return human-readable warning strings for an insecure deployment config.

    Empty list = the profile's security expectations are satisfied. Pure
    logic, no side effects, never raises.
    """
    if s is None:
        s = settings
    profile = normalize_profile(s.deployment_profile)
    warnings: list[str] = []
    origins = s.allowed_origins_list
    hosts = s.trusted_hosts_list
    host_public = _host_is_public(s.jarvis_host)

    if s.deployment_profile not in DEPLOYMENT_PROFILES:
        warnings.append(
            f"JARVIS_DEPLOYMENT_PROFILE='{s.deployment_profile}' is invalid; "
            f"use one of {', '.join(DEPLOYMENT_PROFILES)} (treated as local)."
        )

    if profile == "local":
        if host_public:
            warnings.append(
                "local profile: JARVIS_HOST is not loopback-only "
                f"('{s.jarvis_host}'); local is for localhost-only development."
            )
        if "*" in origins:
            warnings.append(
                "local profile: JARVIS_ALLOWED_ORIGINS contains '*' — permissive "
                "CORS is forbidden unless explicitly intended."
            )
        if "*" in hosts:
            warnings.append(
                "local profile: JARVIS_TRUSTED_HOSTS contains '*' — trusted hosts "
                "should be restricted."
            )
        return warnings

    if not origins:
        warnings.append(
            f"{profile}: JARVIS_ALLOWED_ORIGINS is empty; explicit allowed "
            "origins are required."
        )
    if "*" in origins:
        warnings.append(
            f"{profile}: wildcard CORS ('*') in JARVIS_ALLOWED_ORIGINS is "
            "forbidden — list explicit origins."
        )
    if not hosts:
        warnings.append(
            f"{profile}: JARVIS_TRUSTED_HOSTS is empty; trusted hosts are required."
        )
    if "*" in hosts:
        warnings.append(
            f"{profile}: wildcard host ('*') in JARVIS_TRUSTED_HOSTS is forbidden."
        )
    if not s.require_session_token:
        warnings.append(
            f"{profile}: REQUIRE_SESSION_TOKEN=false — session tokens are "
            "required for this profile."
        )
    if s.jarvis_debug:
        warnings.append(
            f"{profile}: JARVIS_DEBUG=true — debug mode must be disabled."
        )
    if s.jarvis_expose_traces:
        warnings.append(
            f"{profile}: JARVIS_EXPOSE_TRACES=true — detailed per-request trace "
            "exposure should be disabled (set JARVIS_EXPOSE_TRACES=false)."
        )

    if profile == "production":
        if _host_is_loopback(s.jarvis_host):
            warnings.append(
                "production: JARVIS_HOST is loopback-only — localhost binding is "
                "prohibited unless intentionally configured behind a proxy."
            )
        if not s.jarvis_force_https:
            warnings.append(
                "production: JARVIS_FORCE_HTTPS=false — HTTPS must be enforced "
                "(terminate TLS at the reverse proxy and set "
                "JARVIS_BEHIND_REVERSE_PROXY=true)."
            )
        if host_public and not s.jarvis_behind_reverse_proxy:
            warnings.append(
                "production: binding a public host without JARVIS_BEHIND_REVERSE_PROXY=true; "
                "a trusted reverse proxy with TLS must sit in front."
            )
        if not s.jarvis_backup_enabled:
            warnings.append(
                "production: JARVIS_BACKUP_ENABLED=false — database backups are "
                "required (enable and schedule `jarvis-backup`)."
            )
        if s.openrouter_api_key:
            if s.cloud_daily_budget_usd <= 0 and (
                s.cloud_max_request_cost_usd <= 0 or s.cloud_max_session_cost_usd <= 0
            ):
                warnings.append(
                    "production: cloud is configured but no cloud budget is enforced "
                    "(set CLOUD_DAILY_BUDGET_USD and/or request/session caps)."
                )
    else:  # single_host
        if host_public and not s.jarvis_behind_reverse_proxy and not s.jarvis_force_https:
            warnings.append(
                "single_host: a public/bind-all host is exposed without "
                "JARVIS_FORCE_HTTPS or JARVIS_BEHIND_REVERSE_PROXY; use a reverse "
                "proxy with TLS for any exposure beyond loopback."
            )
        if not s.jarvis_backup_enabled:
            warnings.append(
                "single_host: JARVIS_BACKUP_ENABLED=false — enable `jarvis-backup` "
                "for durable persistence."
            )
    return warnings


def public_exposure_safe(s: Settings | None = None) -> bool:
    """True when the app is not exposed to the public internet.

    Loopback binding or placement behind a trusted reverse proxy both count
    as safe. A bind-all or public host without a proxy is unsafe.
    """
    if s is None:
        s = settings
    if _host_is_loopback(s.jarvis_host):
        return True
    return bool(s.jarvis_behind_reverse_proxy)


def cloud_budget_enforced(s: Settings | None = None) -> bool:
    """True when a cloud budget is actually enforced when the cloud is on."""
    if s is None:
        s = settings
    if not s.openrouter_api_key:
        return False
    return (
        s.cloud_daily_budget_usd > 0
        or s.cloud_max_request_cost_usd > 0
        or s.cloud_max_session_cost_usd > 0
    )


def database_backup_configured(s: Settings | None = None) -> bool:
    """True when backups are enabled (tooling available + enabled)."""
    if s is None:
        s = settings
    return bool(s.jarvis_backup_enabled and s.backup_dir)


def deployment_capability_report(s: Settings | None = None) -> dict[str, Any]:
    """Return the deployment capability report exposed on ``GET /runtime``.

    Shape::

        {
          "deployment_profile": "production",
          "runtime_mode": "docker",
          "session_tokens_required": true,
          "debug_enabled": false,
          "cloud_budget_enforced": true,
          "database_backup_configured": true,
          "public_exposure_safe": false,
          "warnings": []
        }

    Never exposes secrets.
    """
    if s is None:
        s = settings
    from jarvis.config.runtime_capabilities import resolve_runtime_mode

    return {
        "deployment_profile": normalize_profile(s.deployment_profile),
        "runtime_mode": resolve_runtime_mode(),
        "session_tokens_required": bool(s.require_session_token),
        "debug_enabled": bool(s.jarvis_debug),
        "cloud_budget_enforced": cloud_budget_enforced(s),
        "database_backup_configured": database_backup_configured(s),
        "public_exposure_safe": public_exposure_safe(s),
        "warnings": validate_deployment(s),
    }


def is_profile(value: str, s: Settings | None = None) -> bool:
    """True when the effective deployment profile equals *value*."""
    if s is None:
        s = settings
    return normalize_profile(s.deployment_profile) == normalize_profile(value)


__all__ = [
    "DEPLOYMENT_PROFILES",
    "cloud_budget_enforced",
    "database_backup_configured",
    "deployment_capability_report",
    "is_profile",
    "normalize_profile",
    "public_exposure_safe",
    "validate_deployment",
]