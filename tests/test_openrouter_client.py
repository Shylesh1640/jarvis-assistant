"""Tests for ``jarvis.models.openrouter_client``.

We monkeypatch ``httpx.post`` so no network is touched, and pin
``settings`` so the fallback chain is deterministic.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from jarvis.models import openrouter_client as orc


@pytest.fixture
def configured_chain(monkeypatch):
    monkeypatch.setattr(orc.settings, "openrouter_api_key", "k", raising=False)
    monkeypatch.setattr(
        orc.settings,
        "complex_model_chain",
        "anthropic/claude-opus-4.1,openai/gpt-5.5",
        raising=False,
    )
    monkeypatch.setattr(
        orc.settings,
        "openrouter_base_url",
        "https://openrouter.ai/api/v1",
        raising=False,
    )
    return orc.settings


def _resp(payload: dict, status: int = 200) -> httpx.Response:
    r = httpx.Response(
        status_code=status, json=payload, request=httpx.Request("POST", "https://x")
    )
    return r


def test_first_model_succeeds(configured_chain, monkeypatch):
    def _fake_post(url, headers, json, timeout):
        assert json["model"] == "anthropic/claude-opus-4.1"
        return _resp({"choices": [{"message": {"content": "hi"}}]})

    monkeypatch.setattr(httpx, "post", _fake_post)
    text, model = orc.run_complex_with_fallback([{"role": "user", "content": "x"}])
    assert text == "hi"
    assert model == "anthropic/claude-opus-4.1"


def test_falls_back_to_second_model(configured_chain, monkeypatch):
    calls = []

    def _fake_post(url, headers, json, timeout):
        calls.append(json["model"])
        if json["model"] == "anthropic/claude-opus-4.1":
            raise httpx.HTTPError("boom")
        return _resp({"choices": [{"message": {"content": "second"}}]})

    monkeypatch.setattr(httpx, "post", _fake_post)
    text, model = orc.run_complex_with_fallback([{"role": "user", "content": "x"}])
    assert text == "second"
    assert model == "openai/gpt-5.5"
    assert calls == ["anthropic/claude-opus-4.1", "openai/gpt-5.5"]


def test_all_fail_raises_runtime_with_each_model_error(configured_chain, monkeypatch):
    def _fake_post(url, headers, json, timeout):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr(httpx, "post", _fake_post)
    with pytest.raises(RuntimeError) as excinfo:
        orc.run_complex_with_fallback([{"role": "user", "content": "x"}])
    msg = str(excinfo.value)
    assert "All complex models failed" in msg
    assert "anthropic/claude-opus-4.1" in msg
    assert "openai/gpt-5.5" in msg


def test_missing_api_key_raises_early(monkeypatch):
    monkeypatch.setattr(orc.settings, "openrouter_api_key", "", raising=False)
    monkeypatch.setattr(
        orc.settings, "complex_model_chain", "anthropic/claude-opus-4.1", raising=False
    )
    monkeypatch.setattr(
        httpx, "post", MagicMock(side_effect=AssertionError("must not POST"))
    )
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        orc.run_complex_with_fallback([])


def test_no_models_configured_raises(configured_chain, monkeypatch):
    monkeypatch.setattr(orc.settings, "complex_model_chain", "", raising=False)
    with pytest.raises(RuntimeError, match="No complex models"):
        orc.run_complex_with_fallback([])


def test_http_status_error_wraps_runtime(configured_chain, monkeypatch):
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _resp({"error": "bad"}, status=404)
    )
    with pytest.raises(RuntimeError, match="HTTP 404"):
        orc.run_complex_with_fallback([{"role": "user", "content": "x"}])


def test_missing_choices_raises(configured_chain, monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _resp({"y": 1}))
    with pytest.raises(RuntimeError, match="did not include"):
        orc.run_complex_with_fallback([{"role": "user", "content": "x"}])


def test_missing_content_raises(configured_chain, monkeypatch):
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _resp({"choices": [{"message": {}}]})
    )
    with pytest.raises(RuntimeError, match="message content"):
        orc.run_complex_with_fallback([{"role": "user", "content": "x"}])
