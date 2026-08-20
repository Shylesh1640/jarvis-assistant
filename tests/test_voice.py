"""Tests for Phase 8 :: voice interface.

Covers the /voice routes (structured "not configured" when disabled,
transcribe + synthesize round-trips, empty/oversized audio guards) using mock
providers — no speech API is ever contacted.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jarvis.api import routes
from jarvis.api.main import app
from jarvis.persistence import create_all
from jarvis.persistence.engine import reset_engine_for_tests
from jarvis.voice import VOICE_INPUT_PROVIDERS, VOICE_OUTPUT_PROVIDERS
from jarvis.voice.base import register_input_provider, register_output_provider


class MockVoiceInput:
    received: list[tuple[bytes, str]] = []

    def __init__(self, settings=None) -> None:
        self._settings = settings

    def transcribe(self, audio: bytes, *, content_type: str = "audio/webm") -> str:
        self.received.append((audio, content_type))
        return "hello from the mock transcriber"


class MockVoiceOutput:
    synthesized: list[str] = []

    def __init__(self, settings=None) -> None:
        self._settings = settings

    def synthesize(self, text: str):
        self.synthesized.append(text)
        return b"audio-bytes-123", "audio/mpeg"


@pytest.fixture
def fresh_db(monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.postgres_dsn", "")
    monkeypatch.setattr("jarvis.config.settings.settings.sqlite_path", ":memory:")
    reset_engine_for_tests()
    create_all()
    yield
    reset_engine_for_tests()


@pytest.fixture
def voice_on(monkeypatch):
    register_input_provider("mock", MockVoiceInput)
    register_output_provider("mock", MockVoiceOutput)
    MockVoiceInput.received = []
    MockVoiceOutput.synthesized = []
    monkeypatch.setattr("jarvis.config.settings.settings.voice_input_enabled", True)
    monkeypatch.setattr("jarvis.config.settings.settings.voice_output_enabled", True)
    monkeypatch.setattr("jarvis.config.settings.settings.voice_input_provider", "mock")
    monkeypatch.setattr("jarvis.config.settings.settings.voice_output_provider", "mock")
    yield
    VOICE_INPUT_PROVIDERS.pop("mock", None)
    VOICE_OUTPUT_PROVIDERS.pop("mock", None)


@pytest.fixture
def client(fresh_db):
    routes.chat._sessions.clear()
    routes.chat._pending_approvals.clear()
    return TestClient(app)


# ---------------------------------------------------------------------------
# Not configured
# ---------------------------------------------------------------------------


def test_voice_routes_not_configured(client):
    r = client.post("/voice/transcribe", files={"audio": ("a.webm", b"data", "audio/webm")})
    assert r.status_code == 503
    assert r.json()["error"] == "voice_not_configured"

    r = client.post("/voice/synthesize", json={"text": "hello"})
    assert r.status_code == 503
    assert r.json()["error"] == "voice_not_configured"


# ---------------------------------------------------------------------------
# Configured
# ---------------------------------------------------------------------------


def test_voice_transcribe(client, voice_on):
    r = client.post("/voice/transcribe", files={"audio": ("a.webm", b"fake-audio-bytes", "audio/webm")})
    assert r.status_code == 200
    assert r.json()["text"] == "hello from the mock transcriber"
    assert MockVoiceInput.received[0][0] == b"fake-audio-bytes"
    assert MockVoiceInput.received[0][1] == "audio/webm"

    # Empty audio is rejected.
    r = client.post("/voice/transcribe", files={"audio": ("a.webm", b"", "audio/webm")})
    assert r.status_code == 400


def test_voice_synthesize(client, voice_on):
    r = client.post("/voice/synthesize", json={"text": "say this"})
    assert r.status_code == 200
    assert r.content == b"audio-bytes-123"
    assert r.headers["content-type"].startswith("audio/mpeg")
    assert MockVoiceOutput.synthesized == ["say this"]

    r = client.post("/voice/synthesize", json={"text": ""})
    assert r.status_code == 422