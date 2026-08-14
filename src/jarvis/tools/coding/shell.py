"""Guarded shell-execution tool.

``run_shell`` accepts a single command string. The first token must be
an allowlisted command (see ``settings.shell_allowed_commands``);
otherwise the call is refused. Destructive patterns are refused even
inside the allowlist. The call is *always* classified as high risk by
``guardrails.risk`` so it always pauses for approval.
"""
from __future__ import annotations

import logging
import re
import shlex
import subprocess
from collections.abc import Iterable

from langchain_core.tools import tool

from jarvis.config.settings import settings
from jarvis.tools.coding.paths import workspace_root

logger = logging.getLogger("jarvis.tools.shell")

# Patterns that are never allowed, even when the head command is permitted.
_BLOCKED_PATTERNS = [
    r"\brm\s+-rf\b",
    r"\brmdir\b",
    r"\bdel\s+/[sfq]\b",
    r"format\b",
    r"shutdown\b",
    r"reboot\b",
    r"\bsudo\b",
    r"DROP\s+TABLE",
    r"DROP\s+DATABASE",
    r"TRUNCATE\b",
    r"mkfs\b",
    r"dd\s+if=",
    r">\s*/dev/sd",
    r":\(\)\s*\{\s*:\|:&\s*\};:",  # fork bomb
]
_BLOCKED_RE = re.compile("|".join(_BLOCKED_PATTERNS), re.IGNORECASE)

# Shell metacharacters / compound-command markers. Blocking these prevents
# chaining a second command onto the allowlisted head ("npm run build; rm -rf
# .") and blocks command substitution / nested execution. With the exception
# of `>` (redirect) they are never needed by the allowlisted commands.
_METACHAR_RE = re.compile(r"[;&|`<]|\$\(|\$\{|\n")


def _allowed_commands() -> set[str]:
    raw = settings.shell_allowed_commands or ""
    return {c.strip().lower() for c in raw.split(",") if c.strip()}


def _head_command(command: str) -> str | None:
    """Return the first token (lowercased) of *command*, or None on parse error."""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        # Windows-style quoting: fall back to whitespace split.
        tokens = command.split()
    return tokens[0].lower() if tokens else None


def _has_blocked_pattern(command: str) -> bool:
    return bool(_BLOCKED_RE.search(command))


def is_safe_command(command: str) -> tuple[bool, str | None]:
    """Return ``(allowed, reason)`` for a candidate shell command."""
    if not command or not command.strip():
        return False, "empty command"
    if _has_blocked_pattern(command):
        return False, "blocked destructive pattern"
    if _has_shell_metacharacters(command):
        return False, "shell metacharacters are not allowed"
    head = _head_command(command)
    if head is None:
        return False, "could not parse command"
    allowed_multi = {a.split()[0].lower() for a in _allowed_commands() if a}
    if head not in allowed_multi:
        return False, f"command '{head}' is not in the allowlist"
    return True, None


def _has_shell_metacharacters(command: str) -> bool:
    """True when *command* contains separators / substitution / redirection.

    Rejects compound commands (``;``, ``&&``, ``||``, ``|``), backgrounding
    (``&``), command substitution (``$(``, backticks, ``${``), input
    redirection (``<``) and embedded newlines, so a command can never chain
    a second (unallowlisted) invocation onto the allowlisted head.
    """
    return bool(_METACHAR_RE.search(command))


@tool
def run_shell(command: str) -> str:
    """Run an allowlisted shell command inside the workspace.

    Returns ``stdout``, ``stderr``, the exit code, and a brief summary.
    Dangerous patterns are refused outright; the call still pauses for
    explicit user approval inside the graph because it's classified high
    risk. Output is truncated to a reasonable size so the model context
    doesn't blow up on noisy commands.
    """
    ok, reason = is_safe_command(command)
    if not ok:
        return f"Error: command refused ({reason})."

    timeout = max(1, settings.tool_subprocess_timeout)
    cwd = str(workspace_root())
    logger.info("run_shell: %r (cwd=%s, timeout=%ds)", command, cwd, timeout)

    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s."
    except OSError as exc:
        return f"Error spawning subprocess: {exc}"

    stdout = (proc.stdout or "")[:4000]
    stderr = (proc.stderr or "")[:2000]
    return (
        f"$ {command}\n"
        f"[exit={proc.returncode}]\n"
        f"--- stdout ---\n{stdout}"
        + (f"\n--- stderr ---\n{stderr}" if stderr else "")
    )


def _summarize_lines(lines: Iterable[str], limit: int) -> str:
    """Helper kept for tests; joins up to *limit* lines with ellipsis."""
    taken = list(lines)[:limit]
    suffix = "" if len(taken) < limit else "\n..."
    return "\n".join(taken) + suffix
