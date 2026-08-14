"""Tests for structured error responses, retry/fallback in branches, and tracing."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jarvis.api import routes
from jarvis.api.main import app
from jarvis.config.settings import settings
from jarvis.observability import clear_traces, recent_traces
from jarvis.observability.trace import finish_trace, new_trace, trace_event
from jarvis.orchestration import branches
from jarvis.orchestration.branches import (
    OllamaModelLoadError,
    OllamaOutOfMemoryError,
    OllamaUnavailableError,
)
from jarvis.orchestration.state import JarvisState


@pytest.fixture
def client(monkeypatch):
    from jarvis.security import ratelimit as ratelimit_module

    clear_traces()
    routes.chat._pending_approvals.clear()
    routes.chat._sessions.clear()
    ratelimit_module.reload_limiter()
    yield TestClient(app)


# ---------------------------------------------------------------------------
# Structured errors through the HTTP layer
# ---------------------------------------------------------------------------


class _BoomGraphOne:
    def __init__(self, exc):
        self._exc = exc

    def invoke(self, state, config=None):
        raise self._exc


@pytest.mark.parametrize(
    "exc,status,code",
    [
        (OllamaUnavailableError("connection refused"), 503, "ollama_unavailable"),
        (OllamaModelLoadError("model 'x' not found"), 502, "model_not_found"),
        (OllamaOutOfMemoryError("CUDA OOM"), 507, "out_of_memory"),
    ],
)
def test_chat_returns_structured_error(client, monkeypatch, exc, status, code):
    clear_traces()
    routes.chat._pending_approvals.clear()
    routes.chat._sessions.clear()
    monkeypatch.setattr(routes.chat, "jarvis_graph", _BoomGraphOne(exc))
    r = client.post(
        "/chat", json={"message": "hi", "session_id": "err-s", "approved": False}
    )
    assert r.status_code == status
    body = r.json()
    assert body["error"] == code
    assert body["message"]
    assert "suggested_action" in body


def test_chat_returns_structured_500_on_unexpected(client, monkeypatch):
    routes.chat._pending_approvals.clear()
    routes.chat._sessions.clear()
    monkeypatch.setattr(routes.chat, "jarvis_graph", _BoomGraphOne(RuntimeError("kaput")))
    r = client.post(
        "/chat", json={"message": "hi", "session_id": "err-s", "approved": False}
    )
    assert r.status_code == 502  # mapped as model_request_failed
    assert r.json()["error"] == "model_request_failed"


def test_unknown_exception_returns_internal_error_code(client):
    from jarvis.api.errors import unexpected_error_to_json

    r = unexpected_error_to_json(RuntimeError("boom"))
    assert r.status_code == 500
    assert r.body.decode().find("internal_error") != -1


# ---------------------------------------------------------------------------
# Retry + GPU→CPU fallback in branches
# ---------------------------------------------------------------------------


class _FakeLLM:
    """Minimal stand-in for a ChatOllama client."""

    def __init__(self, *results):
        self._results = list(results)
        self.calls = 0
        self._force_cpu = False

    def invoke(self, messages):
        self.calls += 1
        if not self._results:
            return _FakeResponse("done")
        item = self._results.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(item)

    def bind_tools(self, tools):
        return self


class _FakeResponse:
    def __init__(self, content):
        self.content = content


def _base_state(**overrides) -> JarvisState:
    state = JarvisState(user_input="test", messages=[], history=[])
    state.update(
        {
            "intent": "coding",
            "complexity": "easy",
            "selected_path": "coding",
        }
    )
    state.update(overrides)
    return state


def test_retry_succeeds_after_transient_failure(monkeypatch):
    monkeypatch.setattr(settings, "retry_max_attempts", 3)
    monkeypatch.setattr(settings, "retry_backoff_seconds", 0.0)
    fake = _FakeLLM(
        OllamaUnavailableError("connection refused"),
        OllamaUnavailableError("connection refused"),
        "done",
    )
    result = branches._invoke_branch_llm(
        _base_state(),
        branch="coding",
        model_name="qwen2.5-coder:7b",
        llm=fake,
        messages=[{"role": "user", "content": "hi"}],
        bound_tools=[],
    )
    assert fake.calls == 3
    assert result.content == "done"


def test_retry_exhausted_raises(monkeypatch):
    monkeypatch.setattr(settings, "retry_max_attempts", 2)
    monkeypatch.setattr(settings, "retry_backoff_seconds", 0.0)
    fake = _FakeLLM(
        OllamaUnavailableError("connection refused"),
        OllamaUnavailableError("connection refused"),
    )
    with pytest.raises(OllamaUnavailableError):
        branches._invoke_branch_llm(
            _base_state(),
            branch="coding",
            model_name="qwen2.5-coder:7b",
            llm=fake,
            messages=[{"role": "user", "content": "hi"}],
            bound_tools=[],
        )
    assert fake.calls == 2


def test_non_retryable_raises_immediately(monkeypatch):
    monkeypatch.setattr(settings, "retry_max_attempts", 3)
    monkeypatch.setattr(settings, "retry_backoff_seconds", 0.0)
    fake = _FakeLLM(OllamaModelLoadError("model 'x' not found"))
    with pytest.raises(OllamaModelLoadError):
        branches._invoke_branch_llm(
            _base_state(),
            branch="coding",
            model_name="qwen2.5-coder:7b",
            llm=fake,
            messages=[{"role": "user", "content": "hi"}],
            bound_tools=[],
        )
    assert fake.calls == 1


def test_gpu_oom_falls_back_to_cpu(monkeypatch):
    monkeypatch.setattr(settings, "retry_max_attempts", 1)
    monkeypatch.setattr(settings, "retry_backoff_seconds", 0.0)
    monkeypatch.setattr(settings, "gpu_fallback_to_cpu", True)

    fake = _FakeLLM(OllamaOutOfMemoryError("CUDA out of memory"))
    cpu_fake = _FakeLLM("done")
    monkeypatch.setattr(branches, "get_model_named", lambda name, **kw: cpu_fake)

    state = _base_state()
    result = branches._invoke_branch_llm(
        state,
        branch="coding",
        model_name="qwen2.5-coder:7b",
        llm=fake,
        messages=[{"role": "user", "content": "hi"}],
        bound_tools=[],
    )
    assert cpu_fake.calls == 1
    assert fake.calls == 1
    assert state.get("fallback_used") == "gpu_to_cpu"
    assert state.get("warning")
    assert result.content == "done"
    assert cpu_fake._force_cpu is True


def test_gpu_fallback_disabled_propagates(monkeypatch):
    monkeypatch.setattr(settings, "retry_max_attempts", 1)
    monkeypatch.setattr(settings, "retry_backoff_seconds", 0.0)
    monkeypatch.setattr(settings, "gpu_fallback_to_cpu", False)
    fake = _FakeLLM(OllamaOutOfMemoryError("CUDA out of memory"))
    with pytest.raises(OllamaOutOfMemoryError):
        branches._invoke_branch_llm(
            _base_state(),
            branch="coding",
            model_name="qwen2.5-coder:7b",
            llm=fake,
            messages=[{"role": "user", "content": "hi"}],
            bound_tools=[],
        )
    assert fake.calls == 1


# ---------------------------------------------------------------------------
# Trace payload
# ---------------------------------------------------------------------------


def test_trace_round_trip():
    clear_traces()
    tr = new_trace(session_id="tr-s")
    trace_event(tr, "started")
    trace_event(tr, "route", intent="coding")
    finish_trace(
        tr,
        result={
            "intent": "coding",
            "complexity": "medium",
            "selected_path": "coding",
            "selected_model": "qwen2.5-coder:7b",
            "tools_used": ["search_code"],
            "risk_level": "low",
            "fallback_used": False,
            "approval_required": False,
        },
    )
    d = tr.to_dict()
    assert d["request_id"]
    assert d["session_id"] == "tr-s"
    assert d["intent"] == "coding"
    assert d["path_used"] == "coding"
    assert d["tools_used"] == ["search_code"]
    assert d["risk_level"] == "low"
    assert d["approval_status"] == "not_required"
    assert d["duration_ms"] >= 0
    assert "error" in d

    dicts = [t.to_dict() for t in recent_traces()]
    assert any(x["request_id"] == d["request_id"] for x in dicts)


def test_trace_records_approval_status():
    tr = new_trace(session_id="tr-approve")
    finish_trace(tr, result={"approval_required": True})
    assert tr.to_dict()["approval_status"] == "required"


def test_trace_event_recorded_after_finish():
    tr = new_trace(session_id="y")
    finish_trace(tr, result={})
    trace_event(tr, "debug")
    assert tr.events[-1][1] == "debug"