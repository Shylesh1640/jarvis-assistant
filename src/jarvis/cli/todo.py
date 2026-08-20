"""CLI: ``jarvis-todo`` — local tasks & reminders (Phase 8).

Usage::

    jarvis-todo list [--session SID] [--status S] [--priority P]
    jarvis-todo add TITLE [--session SID] [--due ISO] [--priority P]
               [--description D] [--yes]
    jarvis-todo complete TODO_ID [--session SID] [--yes]
    jarvis-todo delete TODO_ID [--session SID] [--yes]

Writes (add/complete/delete) prompt for confirmation unless ``--yes`` is
passed. Session-scoped by ``--session`` (default ``default``).
"""
from __future__ import annotations

import argparse
import sys
import uuid

from jarvis.persistence import create_all, repos
from jarvis.todos.domain import (
    TODO_PRIORITIES,
    TODO_STATUSES,
    is_valid_todo_transition,
    normalize_due_at,
)


def _ensure_db() -> None:
    try:
        create_all()
    except Exception:  # noqa: BLE001
        pass


def _confirm(prompt: str, yes: bool) -> bool:
    if yes:
        return True
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="jarvis-todo", description="Jarvis local todos CLI.")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--session", default="default", help="Session id (default: default).")
    sub = parser.add_subparsers(dest="command", required=True)

    list_p = sub.add_parser("list", parents=[common], help="List todos for a session.")
    list_p.add_argument("--status", default=None, help="Filter by status.")
    list_p.add_argument("--priority", default=None, help="Filter by priority.")

    add = sub.add_parser("add", parents=[common], help="Create a todo.")
    add.add_argument("title", help="Todo title.")
    add.add_argument("--due", default=None, help="ISO-8601 due time.")
    add.add_argument("--priority", default="medium", help="low|medium|high.")
    add.add_argument("--description", default=None)
    add.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")

    complete = sub.add_parser("complete", parents=[common], help="Complete a todo.")
    complete.add_argument("todo_id")
    complete.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")

    delete = sub.add_parser("delete", parents=[common], help="Delete (soft-delete) a todo.")
    delete.add_argument("todo_id")
    delete.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    return parser.parse_args(argv)


def _cmd_list(args) -> int:
    if args.status and args.status not in TODO_STATUSES:
        print(f"invalid status; must be one of {list(TODO_STATUSES)}")
        return 2
    if args.priority and args.priority not in TODO_PRIORITIES:
        print(f"invalid priority; must be one of {list(TODO_PRIORITIES)}")
        return 2
    rows = repos.todos.list_for_session(args.session, status=args.status, priority=args.priority)
    if not rows:
        print(f"No todos for session '{args.session}'.")
        return 0
    for r in rows:
        due = f" (due {r.due_at.isoformat()})" if r.due_at else ""
        print(f"[{r.todo_id}] ({r.status}/{r.priority}) {r.title}{due}")
    return 0


def _cmd_add(args) -> int:
    if not args.title.strip():
        print("Error: a title is required.")
        return 2
    if args.priority not in TODO_PRIORITIES:
        print(f"invalid priority; must be one of {list(TODO_PRIORITIES)}")
        return 2
    try:
        due = normalize_due_at(args.due)
    except ValueError as exc:
        print(f"Error: invalid due time ({exc}).")
        return 2
    if not _confirm(f"Create todo '{args.title}' for session '{args.session}'?", args.yes):
        print("Cancelled.")
        return 1
    row = repos.todos.create(
        uuid.uuid4().hex,
        args.session,
        title=args.title.strip(),
        description=args.description,
        priority=args.priority,
        due_at=due,
    )
    print(f"Created todo {row.todo_id}: {row.title} (status={row.status}).")
    return 0


def _cmd_complete(args) -> int:
    row = repos.todos.get(args.session, args.todo_id)
    if row is None:
        print(f"Error: todo {args.todo_id} not found in session '{args.session}'.")
        return 1
    if not is_valid_todo_transition(row.status, "completed"):
        print(f"Error: cannot complete a todo in status '{row.status}'.")
        return 1
    if not _confirm(f"Complete todo '{row.title}'?", args.yes):
        print("Cancelled.")
        return 1
    repos.todos.set_status(args.session, args.todo_id, "completed")
    print(f"Completed todo {args.todo_id}: {row.title}.")
    return 0


def _cmd_delete(args) -> int:
    row = repos.todos.get(args.session, args.todo_id)
    if row is None:
        print(f"Error: todo {args.todo_id} not found in session '{args.session}'.")
        return 1
    if not _confirm(f"Delete todo '{row.title}'?", args.yes):
        print("Cancelled.")
        return 1
    repos.todos.soft_delete(args.session, args.todo_id)
    print(f"Deleted todo {args.todo_id}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _ensure_db()
    if args.command == "list":
        return _cmd_list(args)
    if args.command == "add":
        return _cmd_add(args)
    if args.command == "complete":
        return _cmd_complete(args)
    if args.command == "delete":
        return _cmd_delete(args)
    print(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())