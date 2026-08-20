"""Voice interface abstraction (Phase 8).

Two independent capabilities, each resolved from ``settings`` through its own
registry:

* ``VoiceInputProvider``  — transcribe audio to text (speech-to-text)
* ``VoiceOutputProvider`` — synthesize text to audio (text-to-speech)

No provider ships enabled: ``VOICE_INPUT_ENABLED``/``VOICE_OUTPUT_ENABLED``
default to false and the providers are empty, so the /voice routes return a
structured "not configured" response and never touch an API. Credentials
come from ``VOICE_CREDENTIALS_PATH`` — they are never stored in the DB and
audio data is never logged.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from jarvis.config.settings import settings

VOICE_INPUT_PROVIDERS: dict[str, type["VoiceInputProvider"]] = {}
VOICE_OUTPUT_PROVIDERS: dict[str, type["VoiceOutputProvider"]] = {}


@runtime_checkable
class VoiceInputProvider(Protocol):
    """Speech-to-text backend."""

    def transcribe(self, audio: bytes, *, content_type: str = "audio/webm") -> str:
        """Return the transcribed text for *audio* bytes."""
        ...


@runtime_checkable
class VoiceOutputProvider(Protocol):
    """Text-to-speech backend."""

    def synthesize(self, text: str) -> tuple[bytes, str]:
        """Return (audio bytes, media type e.g. 'audio/mpeg')."""
        ...


def register_input_provider(name: str, cls: type[VoiceInputProvider]) -> None:
    VOICE_INPUT_PROVIDERS[name] = cls


def register_output_provider(name: str, cls: type[VoiceOutputProvider]) -> None:
    VOICE_OUTPUT_PROVIDERS[name] = cls


def get_input_provider() -> VoiceInputProvider | None:
    if not settings.voice_input_enabled:
        return None
    cls = VOICE_INPUT_PROVIDERS.get(settings.voice_input_provider)
    if cls is None:
        return None
    try:
        return cls(settings)
    except Exception:  # noqa: BLE001
        return None


def get_output_provider() -> VoiceOutputProvider | None:
    if not settings.voice_output_enabled:
        return None
    cls = VOICE_OUTPUT_PROVIDERS.get(settings.voice_output_provider)
    if cls is None:
        return None
    try:
        return cls(settings)
    except Exception:  # noqa: BLE001
        return None


def not_configured_message() -> str:
    """Structured, user-actionable reason voice features are unavailable."""
    if not settings.voice_input_enabled and not settings.voice_output_enabled:
        return (
            "Voice is not configured: set VOICE_INPUT_ENABLED=true and/or "
            "VOICE_OUTPUT_ENABLED=true with matching VOICE_INPUT_PROVIDER / "
            "VOICE_OUTPUT_PROVIDER values."
        )
    if not settings.voice_input_enabled:
        return "Voice input is not configured: set VOICE_INPUT_ENABLED=true and VOICE_INPUT_PROVIDER."
    return "Voice output is not configured: set VOICE_OUTPUT_ENABLED=true and VOICE_OUTPUT_PROVIDER."


__all__ = [
    "VOICE_INPUT_PROVIDERS",
    "VOICE_OUTPUT_PROVIDERS",
    "VoiceInputProvider",
    "VoiceOutputProvider",
    "get_input_provider",
    "get_output_provider",
    "not_configured_message",
    "register_input_provider",
    "register_output_provider",
]