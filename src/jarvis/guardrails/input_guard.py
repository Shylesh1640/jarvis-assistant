"""Basic input validation and prompt-injection heuristics.

``validate_input`` returns a ``(is_valid, error_message)`` pair; ``error``
is ``None`` when the input passes. The check is purely rules-based so it
adds no latency and needs no model server.
"""

from __future__ import annotations

# Phrases that look like classic prompt-injection / jailbreak attempts.
BLOCKED_PATTERNS = [
    "ignore previous instructions",
    "disregard all prior",
    "you are now dan",
    "jailbreak",
    "forget all previous",
]

# Hard caps so a pathological client can't exhaust the model's context
# window or churn tokens for minutes on a single request.
_MAX_INPUT_CHARS = 16_000
_MAX_INPUT_LINES = 500


def validate_input(text: str) -> tuple[bool, str | None]:
    """Return ``(is_valid, error_message)``.

    Rejects empty, oversized, or injection-looking inputs. Safe to call
    with ``None``; it is coerced to an empty string.
    """
    if text is None:
        return False, "Input cannot be empty."

    if not text.strip():
        return False, "Input cannot be empty."

    if len(text) > _MAX_INPUT_CHARS:
        return False, f"Input is too long (max {_MAX_INPUT_CHARS} characters)."

    if text.count("\n") + 1 > _MAX_INPUT_LINES:
        return False, f"Input has too many lines (max {_MAX_INPUT_LINES})."

    lowered = text.lower()
    for pattern in BLOCKED_PATTERNS:
        if pattern in lowered:
            return False, "Input rejected by safety filter."

    return True, None
