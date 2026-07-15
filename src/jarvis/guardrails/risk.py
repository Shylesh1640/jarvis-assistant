"""Risk classification for tool actions."""
from typing import Literal

_HIGH_RISK_TOOLS = {
    "write_file",
    "delete_file",
    "remove_file",
    "rmdir",
    "shell_exec",
    "run_command",
    "execute_python",
    "install_package",
    "drop_database",
    "format_disk",
    "shutdown",
}

_MEDIUM_RISK_TOOLS = {
    "read_file",
    "file_write",
    "http_request",
    "pip_install",
    "subprocess_run",
}

_HIGH_RISK_ARGS = {
    "sudo",
    "rm -rf",
    "del /f",
    "format",
    "> /dev/sda",
    "DROP TABLE",
    "DROP DATABASE",
    "TRUNCATE",
}

_SENSITIVE_PATHS = [
    "/etc/",
    "/sys/",
    "/proc/",
    "/boot/",
    "C:\\Windows\\",
    "C:\\Program Files",
    "/.ssh/",
    "/.aws/",
    "/.env",
    ".env.",
    "\\AppData\\Roaming",
]


def _tool_name_risk(name: str) -> Literal["low", "medium", "high"]:
    if name in _HIGH_RISK_TOOLS:
        return "high"
    if name in _MEDIUM_RISK_TOOLS:
        return "medium"
    return "low"


def _tool_args_risk(args: dict) -> Literal["low", "medium", "high"]:
    """Inspect tool arguments for sensitive paths or destructive commands."""
    for val in args.values():
        if not isinstance(val, str):
            continue
        lowered = val.lower()
        for pattern in _HIGH_RISK_ARGS:
            if pattern.lower() in lowered:
                return "high"
        for path in _SENSITIVE_PATHS:
            if path.lower() in lowered:
                return "medium"
    return "low"


def check_tool_risk(
    tool_name: str, tool_args: dict
) -> Literal["low", "medium", "high"]:
    """Classify the risk level of a single tool invocation.

    *name*-based classification is checked first; if it is not already
    ``"high"``, the *args* are inspected for sensitive targets.
    """
    name_risk = _tool_name_risk(tool_name)
    if name_risk == "high":
        return "high"
    args_risk = _tool_args_risk(tool_args)
    if args_risk == "high":
        return "high"
    if name_risk == "medium" or args_risk == "medium":
        return "medium"
    return "low"
