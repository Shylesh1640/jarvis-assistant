"""Central registry of tools available to each orchestration branch.

The registry is the single source of truth for which tools an LLM may
request and which the graph's shared ``ToolNode`` can execute.

* **Safe tools** run automatically after the model requests them (low risk).
* **Approval-gated tools** are bound too — so the model can ask for them —
  but the risk layer pauses them for human approval before the ToolNode
  ever executes them. The approved resume executes exactly the stored tool
  call(s), never a different action.
"""
from __future__ import annotations

from jarvis.tools.coding.file_ops import read_file
from jarvis.tools.coding.git_diff import git_diff
from jarvis.tools.coding.list_directory import list_directory
from jarvis.tools.coding.run_tests import run_tests
from jarvis.tools.coding.shell import run_shell
from jarvis.tools.coding.write_ops import edit_file, write_file
from jarvis.tools.general.calculator import calculator
from jarvis.tools.general.calendar import (
    create_event,
    delete_event,
    list_calendars,
    list_events,
    update_event,
)
from jarvis.tools.general.connectors import list_connectors, run_connector
from jarvis.tools.general.email import (
    create_email_draft,
    delete_email_draft,
    list_email_drafts,
    send_email_draft,
    update_email_draft,
)
from jarvis.tools.general.rag_search import rag_search
from jarvis.tools.general.search_code import search_code
from jarvis.tools.general.todos import (
    complete_todo,
    create_todo,
    delete_todo,
    list_todos,
    update_todo,
)

# Safe, read-only tools — execute automatically (low risk).
GENERAL_TOOLS: list = [
    calculator,
    rag_search,
    search_code,
    read_file,
    list_directory,
    git_diff,
    list_todos,
    list_calendars,
    list_events,
    list_email_drafts,
    list_connectors,
]

CODING_TOOLS: list = [
    calculator,
    search_code,
    read_file,
    list_directory,
    git_diff,
]

# Write/execution tools — requestable by either branch but never executed
# until a human approves the exact tool call (see guardrails/risk.py).
APPROVAL_GATED_TOOLS: list = [
    write_file,
    edit_file,
    run_shell,
    run_tests,
    create_todo,
    complete_todo,
    update_todo,
    delete_todo,
    create_event,
    update_event,
    delete_event,
    create_email_draft,
    update_email_draft,
    delete_email_draft,
    send_email_draft,
    run_connector,
]

# Tools each branch's LLM gets to request.
GENERAL_BOUND_TOOLS: list = GENERAL_TOOLS + APPROVAL_GATED_TOOLS
CODING_BOUND_TOOLS: list = CODING_TOOLS + APPROVAL_GATED_TOOLS


def all_tools() -> list:
    """The deduplicated union the shared ToolNode executes."""
    seen: dict[str, object] = {}
    for tool in GENERAL_BOUND_TOOLS + CODING_BOUND_TOOLS:
        seen.setdefault(tool.name, tool)
    return list(seen.values())


__all__ = [
    "GENERAL_TOOLS",
    "CODING_TOOLS",
    "APPROVAL_GATED_TOOLS",
    "GENERAL_BOUND_TOOLS",
    "CODING_BOUND_TOOLS",
    "all_tools",
]