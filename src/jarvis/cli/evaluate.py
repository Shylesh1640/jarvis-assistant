"""CLI: review stored user feedback and produce a short evaluation report.

Phase 6 :: Feedback quality. The chat UI lets users rate replies
(good / bad / unclear, plus an optional comment). This CLI summarises what
has been collected so an operator can spot patterns:

Usage::

    uv run jarvis-evaluate                          # summary stats + bad items
    uv run jarvis-evaluate --detail                 # show every entry
    uv run jarvis-evaluate --score bad              # filter by score
    uv run jarvis-evaluate --clear                  # wipe all feedback
    uv run jarvis-evaluate --limit 50               # cap items shown

Exits 0 on success; non-zero only when the DB is unavailable or a
hard error occurs. No LLM is involved — the report is purely statistical
over the stored rows.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter

from jarvis.persistence import create_all, repos


def _ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="jarvis-evaluate",
        description="Summarise stored user feedback on assistant replies.",
    )
    parser.add_argument("--detail", action="store_true", help="Print every feedback entry.")
    parser.add_argument("--score", choices=("good", "bad", "unclear"), default=None,
                        help="Only show entries with this score.")
    parser.add_argument("--limit", type=int, default=50, help="Cap entries shown (default 50).")
    parser.add_argument("--clear", action="store_true", help="Delete all stored feedback.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        create_all()
    except Exception as exc:  # noqa: BLE001
        _fail(f"Persistence layer unavailable: {exc}")
        return 1

    if args.clear:
        removed = repos.feedback.delete_all()
        print(f"Cleared {removed} feedback entr(ies).")
        return 0

    try:
        rows = repos.feedback.list(limit=args.limit if args.limit > 0 else None)
    except Exception as exc:  # noqa: BLE001
        _fail(f"Reading feedback failed: {exc}")
        return 1

    print("Jarvis feedback evaluation")
    print("=" * 40)
    if not rows:
        print("No feedback collected yet. Rate replies in the UI to build data.")
        return 0

    counter = Counter(r.score for r in rows)
    print(f"Total entries: {len(rows)}")
    for score in ("good", "bad", "unclear"):
        n = counter.get(score, 0)
        pct = 100.0 * n / len(rows)
        print(f"  {score:<8} {n:>4}  ({pct:.1f}%)")

    models = Counter(r.model_used or "unknown" for r in rows)
    print("\nBy model:")
    for model, n in models.most_common():
        print(f"  {model:<24} {n:>4}")

    if args.detail or args.score:
        shown = [r for r in rows if args.score is None or r.score == args.score]
        shown = shown[: args.limit] if args.limit > 0 else shown
        print(f"\nEntries ({len(shown)} shown):")
        for r in shown:
            print("-" * 40)
            print(f"#{r.id} [{r.score}] {r.created_at.isoformat() if r.created_at else ''}")
            print(f"  Q: {r.question[:120]}")
            print(f"  A: {r.answer[:200]}")
            if r.comment:
                print(f"  Comment: {r.comment[:200]}")
            if r.path_used or r.model_used:
                print(f"  path={r.path_used} model={r.model_used}")

    bad = counter.get("bad", 0)
    if bad:
        _warn(f"{bad} reply(ies) rated bad — review them for patterns.")
    _ok("Evaluation complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())