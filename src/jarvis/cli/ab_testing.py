"""CLI entry point for A/B testing (``uv run jarvis-ab-test``).

Thin wrapper around :mod:`jarvis.ab_testing.manager`. Exits 0 on success and
a non-zero code on failure. No secrets are printed — output is redacted.
"""
from __future__ import annotations

import sys

from jarvis.ab_testing.manager import main


def cli() -> None:
    sys.exit(main())


if __name__ == "__main__":
    cli()
