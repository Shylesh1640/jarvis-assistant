"""CLI: ``jarvis-email`` — email drafts (Phase 8).

Usage::

    jarvis-email draft --subject SUBJ --recipients a@b.com,c@d.com [--body B]
                       [--session SID] [--from ADDR]
    jarvis-email list [--session SID]
    jarvis-email send DRAFT_ID [--session SID] [--yes]

Draft/create/list work locally with no provider. ``send`` prompts for
confirmation unless ``--yes`` and requires a configured ``EMAIL_PROVIDER``
(else it prints the structured "not configured" message and exits 1).
No secrets or full bodies are ever printed.
"""
from __future__ import annotations

import argparse
import sys
import uuid

from jarvis.email import get_provider, not_configured_message
from jarvis.email.drafts import validate_recipients
from jarvis.persistence import create_all, repos


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
    parser = argparse.ArgumentParser(prog="jarvis-email", description="Jarvis email drafts CLI.")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--session", default="default", help="Session id (default: default).")
    sub = parser.add_subparsers(dest="command", required=True)

    draft = sub.add_parser("draft", parents=[common], help="Create a local email draft.")
    draft.add_argument("--subject", required=True)
    draft.add_argument("--recipients", required=True, help="Comma-separated addresses.")
    draft.add_argument("--body", default=None)
    draft.add_argument("--from", dest="from_address", default=None)

    sub.add_parser("list", parents=[common], help="List drafts for a session.")

    send = sub.add_parser("send", parents=[common], help="Send a draft (needs provider).")
    send.add_argument("draft_id")
    send.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    return parser.parse_args(argv)


def _cmd_draft(args) -> int:
    recipients = [r.strip() for r in args.recipients.split(",") if r.strip()]
    error = validate_recipients(recipients)
    if error:
        print(f"Error: {error}")
        return 2
    row = repos.email_drafts.create(
        uuid.uuid4().hex,
        args.session,
        subject=args.subject,
        recipients=recipients,
        body=args.body,
        from_address=args.from_address,
    )
    print(f"Created email draft {row.draft_id}: {row.subject} → {', '.join(recipients)}.")
    return 0


def _cmd_list(args) -> int:
    rows = repos.email_drafts.list_for_session(args.session)
    if not rows:
        print(f"No email drafts for session '{args.session}'.")
        return 0
    for r in rows:
        print(
            f"[{r.draft_id}] ({r.status}) {r.subject} → {', '.join(r.recipients or [])}"
        )
    return 0


def _cmd_send(args) -> int:
    row = repos.email_drafts.get(args.session, args.draft_id)
    if row is None:
        print(f"Error: email draft {args.draft_id} not found in session '{args.session}'.")
        return 1
    provider = get_provider()
    if provider is None:
        print(not_configured_message())
        return 1
    if not _confirm(f"Send email draft '{row.subject}' to {', '.join(row.recipients or [])}?", args.yes):
        print("Cancelled.")
        return 1
    message_id = provider.send(
        subject=row.subject,
        recipients=list(row.recipients or []),
        body=row.body,
        from_address=row.from_address,
    )
    repos.email_drafts.mark_sent(args.session, args.draft_id)
    print(f"Sent email draft {args.draft_id} (message {message_id}).")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _ensure_db()
    if args.command == "draft":
        return _cmd_draft(args)
    if args.command == "list":
        return _cmd_list(args)
    if args.command == "send":
        return _cmd_send(args)
    print(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())