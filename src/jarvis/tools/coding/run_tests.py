"""Run the test suite for a directory under the workspace."""
from __future__ import annotations

import logging
import subprocess

from langchain_core.tools import tool

from jarvis.config.settings import settings
from jarvis.tools.coding.paths import WorkspaceError, resolve_in_workspace

logger = logging.getLogger("jarvis.tools.run_tests")


def _candidate_commands(target: str) -> list[list[str]]:
    """Build the pytest invocation list-of-args variants to try in order."""
    base = [
        "uv",
        "run",
        "python",
        "-m",
        "pytest",
        "-q",
        "--no-header",
        "-x",
        "--tb=short",
    ]
    args = base + [target]
    return [args, ["python", "-m", "pytest", "-q", "-x", "--tb=short", target]]


@tool
def run_tests(target: str = ".") -> str:
    """Run ``pytest`` in *target* (a directory under the workspace).

    Returns stdout+stderr and a summary line. ``target`` must resolve
    inside the workspace root. Capped to ``settings.tool_subprocess_timeout``
    seconds so a hung suite can't stall the assistant.
    """
    try:
        resolved = resolve_in_workspace(target, must_exist=True)
    except WorkspaceError as exc:
        return f"Error: {exc}"

    timeout = max(1, settings.tool_subprocess_timeout)
    logger.info("run_tests: target=%s (cwd=%s, timeout=%ds)", target, resolved, timeout)

    last: str | None = None
    for args in _candidate_commands(str(resolved)):
        try:
            proc = subprocess.run(
                args,
                cwd=str(resolved.parent) if resolved.is_file() else str(resolved),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError:
            last = f"Error: {args[0]} not found"
            continue
        except subprocess.TimeoutExpired:
            return f"Error: tests timed out after {timeout}s."

        stdout = (proc.stdout or "")[:8000]
        stderr = (proc.stderr or "")[:2000]
        summary = _summarize(stdout) or "(no output)"
        return (
            f"$ {' '.join(args)}\n"
            f"[exit={proc.returncode}]\n"
            f"--- summary ---\n{summary}\n"
            f"--- stdout ---\n{stdout}"
            + (f"\n--- stderr ---\n{stderr}" if stderr else "")
        )

    return last or "Error: no test runner found."


def _summarize(stdout: str) -> str:
    """Pull the trailing pytest summary line(s) out of *stdout*."""
    kept: list[str] = []
    for line in stdout.splitlines():
        if "passed" in line or "failed" in line or "error" in line:
            kept.append(line.strip())
    return "\n".join(kept[-3:]) if kept else ""
