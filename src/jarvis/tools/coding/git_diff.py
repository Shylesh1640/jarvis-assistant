"""Git diff tool — read-only view of repository changes.

Runs ``git diff`` inside the workspace and returns the output. Classified
low risk by ``guardrails.risk`` (read-only), so it does NOT need approval.
The command is restricted to ``git diff`` with optional flags — no
arbitrary git subcommands.
"""
from __future__ import annotations

import logging
import subprocess

from langchain_core.tools import tool

from jarvis.config.settings import settings
from jarvis.tools.coding.paths import workspace_root

logger = logging.getLogger("jarvis.tools.git_diff")

_MAX_DIFF_BYTES = 8000
_ALLOWED_FLAGS = {"--stat", "--name-only", "--cached", "--staged", "--numstat", "-U0", "-U1", "-U2", "-U3"}


def _validate_flags(flags: str) -> tuple[list[str], str | None]:
    """Return (allowed_args, error_msg)."""
    parts = flags.split() if flags else []
    out: list[str] = []
    for f in parts:
        if f in _ALLOWED_FLAGS:
            out.append(f)
        else:
            return [], f"Unsupported git diff flag: {f!r}. Allowed: {sorted(_ALLOWED_FLAGS)}"
    return out, None


@tool
def git_diff(flags: str = "") -> str:
    """Show the git diff of the workspace repository.

    Optional *flags* controls the diff style. Only a small set of read-only
    flags is accepted (``--stat``, ``--name-only``, ``--cached``,
    ``--numstat``, ``-U0``..``-U3``). Returns the diff output truncated to
    a reasonable size. If the workspace is not a git repository, returns a
    clear error.
    """
    allowed, err = _validate_flags(flags)
    if err:
        return f"Error: {err}"

    cmd = ["git", "diff"] + allowed
    timeout = max(5, min(settings.tool_subprocess_timeout, 30))
    cwd = str(workspace_root())
    logger.info("git_diff: %s (cwd=%s)", " ".join(cmd), cwd)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"Error: git diff timed out after {timeout}s."
    except FileNotFoundError:
        return "Error: git is not installed or not on PATH."
    except OSError as exc:
        return f"Error running git diff: {exc}"

    if proc.returncode != 0 and "not a git repository" in (proc.stderr or "").lower():
        return "Error: the workspace is not a git repository."
    if proc.returncode != 0:
        err_msg = (proc.stderr or "").strip()[:500]
        return f"Error: git diff exited {proc.returncode}: {err_msg}"

    diff = (proc.stdout or "").strip()
    if not diff:
        return "(no changes — working tree is clean)"
    if len(diff) > _MAX_DIFF_BYTES:
        diff = diff[:_MAX_DIFF_BYTES] + "\n... (truncated)"
    return diff
