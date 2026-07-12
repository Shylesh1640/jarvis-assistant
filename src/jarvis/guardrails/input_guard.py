"""Basic input validation and prompt-injection heuristics."""

BLOCKED_PATTERNS = [
    "ignore previous instructions",
    "disregard all prior",
    "you are now dan",
]


def validate_input(text: str) -> tuple[bool, str | None]:
    """Return (is_valid, error_message)."""
    if not text or not text.strip():
        return False, "Input cannot be empty."

    lowered = text.lower()
    for pattern in BLOCKED_PATTERNS:
        if pattern in lowered:
            return False, "Input rejected by safety filter."

    return True, None
