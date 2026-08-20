"""Shared email validation (Phase 8).

Kept dependency-free (no ``email-validator``) so routes, tools and the CLI
agree on what a valid address is. Deliberately permissive — it checks shape,
not deliverability.
"""
from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(address: str) -> bool:
    """True for a plausibly-shaped single email address."""
    return bool(_EMAIL_RE.match(address or ""))


__all__ = ["is_valid_email"]