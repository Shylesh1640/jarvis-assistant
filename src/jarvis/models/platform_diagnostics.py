"""Read-only Docker + WSL platform diagnostics.

All functions are best-effort and never raise: every probe returns a
structured result with a ``warnings`` list. They are strictly read-only —
no start/stop/pull/prune/down commands are ever run — so calling them from
the runtime endpoint or validation CLI is always safe.

Docker:
    * ``docker_cli_available`` — ``docker`` on PATH (no subprocess)
    * ``docker_daemon_reachable`` — ``docker info`` succeeds (timeout-bounded)
    * ``get_docker_containers`` — ``docker ps`` -> names/status/image
    * ``get_docker_disk_usage`` — ``docker system df`` -> per-type totals
    * ``get_docker_wsl_diagnostics`` — aggregate block for the /runtime view

WSL (Windows):
    * ``get_wsl_info`` — ``wsl --list --verbose`` parsing + .wslconfig presence.
      The .wslconfig file is inspected for tuning keys (memory=, processors=,
      swap=, autoMemoryReclaim) but its values/content are NEVER returned.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

# Short timeouts so a stalled daemon / WSL never blocks the request path.
_DOCKER_TIMEOUT = 5.0
_WSL_TIMEOUT = 8.0

# Keys we look for in %USERPROFILE%\.wslconfig (presence only, never values).
_WSL_CONFIG_KEYS = ("memory", "processors", "swap", "autoMemoryReclaim")


# ---------------------------------------------------------------------------
# Low-level probe helpers
# ---------------------------------------------------------------------------


def _exec(cmd: list[str], timeout: float) -> tuple[int | None, str, str]:
    """Run *cmd* and return (returncode|None, stdout, stderr).

    ``None`` returncode means the executable was missing or the call raised
    (including a timeout). Never raises.
    """
    exe = shutil.which(cmd[0])
    if exe is None:
        return None, "", f"`{cmd[0]}` not found on PATH."
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        return None, "", f"`{cmd[0]}` timed out after {timeout:g}s."
    except Exception as exc:  # noqa: BLE001
        return None, "", f"`{cmd[0]}` failed: {exc.__class__.__name__}"


def docker_cli_available() -> bool:
    """True when the ``docker`` CLI is on PATH (no subprocess launched)."""
    return shutil.which("docker") is not None


def docker_daemon_reachable() -> tuple[bool, list[str]]:
    """Return (reachable, warnings) by querying ``docker info``."""
    if not docker_cli_available():
        return False, ["`docker` CLI not found on PATH."]
    rc, out, err = _exec(["docker", "info"], timeout=_DOCKER_TIMEOUT)
    if rc is None:
        return False, [err or "`docker info` failed."]
    if rc != 0:
        return False, [f"`docker info` exited {rc}: {err.strip()[:200]}"]
    return True, []


def get_docker_containers() -> tuple[list[dict[str, str]], list[str]]:
    """Return (containers, warnings) from ``docker ps`` (running only).

    Each item: {"name", "status", "image"}. No exit-code or uptime parsing
    of stopped containers — this is a snapshot of what is currently running.
    """
    if not docker_cli_available():
        return [], ["`docker` CLI not found on PATH."]
    rc, out, err = _exec(["docker", "ps", "--format", "{{.Names}}\t{{.Status}}\t{{.Image}}"], timeout=_DOCKER_TIMEOUT)
    if rc is None:
        return [], [err or "`docker ps` failed."]
    if rc != 0:
        return [], [f"`docker ps` exited {rc}: {err.strip()[:200]}"]
    rows: list[dict[str, str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        rows.append({"name": parts[0], "status": parts[1], "image": parts[2]})
    return rows, []


def get_docker_disk_usage() -> tuple[dict[str, Any], list[str]]:
    """Return (usage, warnings) from ``docker system df``.

    usage maps a type (images/containers/volumes/build_cache) to
    {"total", "active", "size", "reclaimable"} counts/sizes as reported by
    the CLI (strings — no numeric conversion needed for display).
    """
    if not docker_cli_available():
        return {}, ["`docker` CLI not found on PATH."]
    rc, out, err = _exec(
        ["docker", "system", "df", "--format", "{{.Type}}\t{{.TotalCount}}\t{{.Active}}\t{{.Size}}\t{{.Reclaimable}}"],
        timeout=_DOCKER_TIMEOUT,
    )
    if rc is None:
        return {}, [err or "`docker system df` failed."]
    if rc != 0:
        return {}, [f"`docker system df` exited {rc}: {err.strip()[:200]}"]
    usage: dict[str, Any] = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        usage[parts[0]] = {
            "total": parts[1],
            "active": parts[2],
            "size": parts[3],
            "reclaimable": parts[4],
        }
    return usage, []


# ---------------------------------------------------------------------------
# WSL probes
# ---------------------------------------------------------------------------


def get_wsl_info() -> tuple[dict[str, Any], list[str]]:
    """Return (info, warnings) about WSL.

    info:
        available           — ``wsl`` CLI on PATH
        wsl2_enabled        — default distribution runs WSL2 (version 2)
        default_distro      — name of the default distribution, if any
        distributions       — list of {"name", "state", "version"}
        config_present      — %USERPROFILE%\\.wslconfig exists
        config_keys         — {"memory": bool, "processors": bool,
                               "swap": bool, "autoMemoryReclaim": bool}
                              (presence only — values never returned)
    """
    warnings: list[str] = []
    available = shutil.which("wsl") is not None
    info: dict[str, Any] = {
        "available": available,
        "wsl2_enabled": False,
        "default_distro": None,
        "distributions": [],
        "config_present": False,
        "config_keys": {},
    }
    if not available:
        warnings.append("`wsl` CLI not found on PATH (WSL2 not available).")
    else:
        rc, out, err = _exec(["wsl", "--list", "--verbose"], timeout=_WSL_TIMEOUT)
        if rc is None:
            warnings.append(err or "`wsl --list --verbose` failed.")
        elif rc != 0:
            warnings.append(f"`wsl --list --verbose` exited {rc}: {err.strip()[:200]}")
        else:
            info["distributions"] = _parse_wsl_list(out)
            info["default_distro"] = next(
                (d["name"] for d in info["distributions"] if d.get("default")), None
            )
            info["wsl2_enabled"] = any(d.get("version") == 2 for d in info["distributions"])

    info["config_present"], info["config_keys"] = _wslconfig_presence()
    return info, warnings


def _parse_wsl_list(stdout: str) -> list[dict[str, Any]]:
    """Parse ``wsl --list --verbose`` output.

    Layout (Windows):
        NAME                   STATE           VERSION
      * Ubuntu                 Running         2

    Tolerates extra spaces, the leading ``*`` default marker, and missing
    rows. Version is coerced to int when it parses, else left as-is.
    """
    rows: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        stripped = line.strip()
        if "NAME" in stripped and "STATE" in stripped and "VERSION" in stripped:
            continue  # header
        is_default = stripped.startswith("*")
        rest = stripped.lstrip("* ").strip()
        if not rest:
            continue
        # NAME can be a single token; STATE and VERSION are the last two.
        parts = rest.split()
        if len(parts) < 2:
            continue
        name = parts[0]
        version_raw = parts[-1]
        state = parts[-2]
        try:
            version: Any = int(version_raw)
        except ValueError:
            version = version_raw
        rows.append({
            "name": name,
            "state": state,
            "version": version,
            "default": is_default,
        })
    return rows


def _wslconfig_presence() -> tuple[bool, dict[str, bool]]:
    """Return (config_present, {key: present}) for %USERPROFILE%\\.wslconfig.

    Only *presence* of the tuning keys is reported — the file content is
    never exposed (no memory sizes, no paths).
    """
    cfg = os.path.join(os.path.expanduser("~"), ".wslconfig")
    if not os.path.isfile(cfg):
        return False, {}
    keys: dict[str, bool] = {}
    try:
        with open(cfg, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except Exception:  # noqa: BLE001
        logger.warning("Could not read .wslconfig for presence check.", exc_info=True)
        return True, {k: False for k in _WSL_CONFIG_KEYS}
    for key in _WSL_CONFIG_KEYS:
        keys[key] = re.search(rf"(?m)^\s*{re.escape(key)}\s*=", text) is not None
    return True, keys


# ---------------------------------------------------------------------------
# Aggregate block for the /runtime view
# ---------------------------------------------------------------------------


def get_docker_wsl_diagnostics() -> dict[str, Any]:
    """Aggregate the Docker + WSL diagnostics into one structured block.

    Shape:
        {
          "docker": {"cli_available", "daemon_reachable", "containers",
                     "disk_usage", "warnings"},
          "wsl":    {info..., "warnings"},
        }

    Best-effort: if Docker/WSL are missing entirely the block still renders
    with ``available=False`` and an explanatory warning.
    """
    docker_warnings: list[str] = []
    cli_available = docker_cli_available()
    if cli_available:
        daemon_reachable, daemon_warns = docker_daemon_reachable()
        docker_warnings.extend(daemon_warns)
        if daemon_reachable:
            _, ps_warns = get_docker_containers()
            docker_warnings.extend(ps_warns)
            _, df_warns = get_docker_disk_usage()
            docker_warnings.extend(df_warns)
    else:
        daemon_reachable = False
        docker_warnings.append("`docker` CLI not found on PATH.")

    wsl_info, wsl_warnings = get_wsl_info()
    return {
        "docker": {
            "cli_available": cli_available,
            "daemon_reachable": daemon_reachable,
            "containers": get_docker_containers()[0] if (cli_available and daemon_reachable) else [],
            "disk_usage": get_docker_disk_usage()[0] if (cli_available and daemon_reachable) else {},
            "warnings": docker_warnings,
        },
        "wsl": {**wsl_info, "warnings": wsl_warnings},
    }


__all__ = [
    "docker_cli_available",
    "docker_daemon_reachable",
    "get_docker_containers",
    "get_docker_disk_usage",
    "get_wsl_info",
    "get_docker_wsl_diagnostics",
    "_parse_wsl_list",
    "_wslconfig_presence",
]