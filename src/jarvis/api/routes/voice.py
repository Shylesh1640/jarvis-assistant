"""Routes for the voice interface (Phase 8).

* ``POST /voice/transcribe`` — upload audio (multipart ``audio``) -> text
* ``POST /voice/synthesize`` — JSON ``{"text": ...}`` -> audio bytes

Both require their provider to be explicitly configured via
``VOICE_INPUT_ENABLED``/``VOICE_OUTPUT_ENABLED`` + provider name; otherwise a
structured ``503 voice_not_configured`` response is returned and no speech
API is ever contacted. Audio data is never logged and credentials never
leave the provider.
"""
from __future__ import annotations

from fastapi import APIRouter, File, Response, UploadFile

from jarvis.api.errors import APIError
from jarvis.api.schemas.voice import VoiceSynthesize
from jarvis.security.session_auth import ensure_session_context
from jarvis.voice import (
    get_input_provider,
    get_output_provider,
    not_configured_message,
)

router = APIRouter(prefix="/voice", tags=["voice"])

_MAX_AUDIO_BYTES = 25 * 1024 * 1024  # 25 MB cap so a stray upload can't OOM


@router.post("/transcribe")
async def voice_transcribe(audio: UploadFile = File(...)) -> dict:
    ensure_session_context("default", None)
    provider = get_input_provider()
    if provider is None:
        raise APIError(503, "voice_not_configured", not_configured_message())
    data = await audio.read()
    if not data:
        raise APIError(400, "empty_audio", "No audio data received.")
    if len(data) > _MAX_AUDIO_BYTES:
        raise APIError(413, "audio_too_large", f"Audio exceeds the {_MAX_AUDIO_BYTES // (1024 * 1024)}MB limit.")
    content_type = audio.content_type or "audio/webm"
    text = provider.transcribe(data, content_type=content_type)
    if not text or not text.strip():
        raise APIError(422, "no_speech", "No speech could be transcribed from the audio.")
    return {"text": text.strip()}


@router.post("/synthesize")
def voice_synthesize(payload: VoiceSynthesize) -> Response:
    ensure_session_context("default", None)
    provider = get_output_provider()
    if provider is None:
        raise APIError(503, "voice_not_configured", not_configured_message())
    audio, media_type = provider.synthesize(payload.text)
    if not audio:
        raise APIError(502, "synthesis_failed", "The voice provider returned no audio.")
    return Response(content=audio, media_type=media_type)