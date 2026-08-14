"""Basic output validation and PII redaction.

Redaction is intentionally conservative: we prefer a few false-positives
that replace a digit run with a placeholder over leaking a real identifier,
and we always return a ``str`` so callers can chain ``redact_output(...)``
without an extra ``isinstance`` guard even when the LLM returns ``None``.

Order matters: more specific / longer patterns are matched first so a
greedy phone regex can't shadow credit-card, SSN, or IP matches.
"""

from __future__ import annotations

import re

# Email addresses.
EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

# US-style Social Security numbers: XXX-XX-XXXX (dash form only, to avoid
# matching arbitrary 9-digit runs).
SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

# Credit-card-style 13–19 digit runs. Digits may be separated by spaces or
# dashes; lookarounds anchor on digit neighbours so we don't match
# substrings of longer digit blobs.
CREDIT_CARD_PATTERN = re.compile(r"(?<!\d)(\d[ -]?){13,19}(?!\d)")

# 10-digit North American phone numbers, with `()-`/spaces/dots/dashes
# allowed only between the 3-3-4 groups. Anchored with lookarounds so it
# doesn't bleed into a larger digit run (cards, IPs).
PHONE_PATTERN = re.compile(
    r"(?<!\d)"
    r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"
    r"(?!\d)"
)

# Bare IPv4 addresses.
IPV4_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# --- Secrets (hardening) ---

# AWS-style access key ids.
AWS_KEY_PATTERN = re.compile(r"\bAKIA[0-9A-Z]{16}\b")

# Generic sk-… / ghp_… / xox… style API tokens (OpenAI, GitHub, Slack, …).
API_TOKEN_PATTERN = re.compile(
    r"\b(?:sk|rk|pk|gh[pousr]_|gsk_|xox[baprs]-?)[A-Za-z0-9_\-]{16,}\b"
)

# PEM private-key blocks.
PRIVATE_KEY_BLOCK_PATTERN = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)

# Explicit secret assignments: NAME=value / NAME: value for common secret
# names (except model/context config keys). Redacts the assignment.
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(api[_\-]?key|secret([_\-]?key)?|passw(or)?d|auth[_\-]?token|"
    r"access[_\-]?token|priv(ate)?[_\-]?key)\b\s*[:=]\s*\S+"
)


def redact_output(text: object) -> str:
    """Redact common PII and secret patterns from *text*.

    Returns a ``str`` regardless of input: ``None`` or non-string payloads
    are stringified first so callers can chain this without a guard.
    """
    if not isinstance(text, str):
        text = "" if text is None else str(text)

    # Long / specific patterns first so a greedy phone/card regex can't
    # shadow secret tokens that embed digit runs (e.g. ghp_…, xoxb-…).
    text = EMAIL_PATTERN.sub("[redacted-email]", text)
    text = PRIVATE_KEY_BLOCK_PATTERN.sub("[redacted-private-key]", text)
    text = AWS_KEY_PATTERN.sub("[redacted-aws-key]", text)
    text = API_TOKEN_PATTERN.sub("[redacted-token]", text)
    text = CREDIT_CARD_PATTERN.sub("[redacted-card]", text)
    text = SSN_PATTERN.sub("[redacted-ssn]", text)
    text = PHONE_PATTERN.sub("[redacted-phone]", text)
    text = IPV4_PATTERN.sub("[redacted-ip]", text)
    text = SECRET_ASSIGNMENT_PATTERN.sub("[redacted-secret]", text)
    return text
