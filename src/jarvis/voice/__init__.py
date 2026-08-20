"""Voice interface (Phase 8)."""
from jarvis.voice.base import (
    VOICE_INPUT_PROVIDERS,
    VOICE_OUTPUT_PROVIDERS,
    VoiceInputProvider,
    VoiceOutputProvider,
    get_input_provider,
    get_output_provider,
    not_configured_message,
    register_input_provider,
    register_output_provider,
)

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