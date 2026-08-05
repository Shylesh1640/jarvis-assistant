"""Tests for the PII redaction layer in ``jarvis.guardrails.output_guard``.

Covers email, phone, SSN, credit-card, and IP patterns plus the
non-string / None handling that lets callers chain ``redact_output``
without an extra ``isinstance`` guard.
"""

from jarvis.guardrails.output_guard import redact_output


def test_redact_passthrough_without_pii():
    assert redact_output("nothing to redact here") == "nothing to redact here"


def test_redact_email():
    out = redact_output("contact me at jane.doe@example.com please")
    assert "[redacted-email]" in out
    assert "jane.doe@example.com" not in out


def test_redact_multiple_emails():
    out = redact_output("a@b.io and c@d.io")
    assert out.count("[redacted-email]") == 2


def test_redact_plain_phone():
    out = redact_output("call 5551234567")
    assert "[redacted-phone]" in out
    assert "5551234567" not in out


def test_redact_dashed_phone():
    out = redact_output("call 555-123-4567 now")
    assert "[redacted-phone]" in out


def test_redact_paren_phone():
    out = redact_output("call (555) 123-4567 now")
    assert "[redacted-phone]" in out


def test_redact_ssn():
    out = redact_output("ssn 123-45-6789")
    assert "[redacted-ssn]" in out
    assert "123-45-6789" not in out


def test_redact_credit_card():
    out = redact_output("card 4111111111111111 done")
    assert "[redacted-card]" in out
    assert "4111111111111111" not in out


def test_redact_credit_card_with_separators():
    out = redact_output("card 4111 1111 1111 1111 done")
    assert "[redacted-card]" in out


def test_redact_ipv4():
    out = redact_output("ping 192.168.0.1 please")
    assert "[redacted-ip]" in out


def test_redact_handles_none_as_empty():
    assert redact_output(None) == ""


def test_redact_handles_non_string():
    out = redact_output(12345)
    assert out == "12345"
    assert "[redacted-" not in out


def test_redact_does_not_touch_short_digit_runs():
    """5-digit zip codes / small integers should survive untouched."""
    assert redact_output("zip 90210") == "zip 90210"


def test_redact_preserves_normal_periods_in_sentences():
    out = redact_output("This sentence is fine. So is this one.")
    assert out == "This sentence is fine. So is this one."
