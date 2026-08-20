"""Tests for the per-request trace module."""
from jarvis.config.settings import settings
from jarvis.observability import (
    Trace,
    clear_traces,
    finish_trace,
    new_trace,
    recent_traces,
    trace_event,
)


def test_new_trace_starts_events_empty():
    clear_traces()
    tr = new_trace(session_id="s1")
    assert isinstance(tr, Trace)
    assert tr.events == []
    assert tr.session_id == "s1"
    assert tr.duration_ms >= 0.0


def test_trace_event_appends_with_relative_time():
    clear_traces()
    tr = new_trace(session_id="s1")
    trace_event(tr, "first", foo="bar")
    assert len(tr.events) == 1
    rel, label, extra = tr.events[0]
    assert label == "first"
    assert extra == {"foo": "bar"}
    assert rel >= 0.0


def test_finish_trace_logs_summary():
    clear_traces()
    tr = new_trace(session_id="s1", approved=False)
    trace_event(tr, "graph_completed")
    finish_trace(tr, result={"intent": "general", "complexity": "easy",
                              "selected_path": "general", "selected_model": "qwen3",
                              "risk_level": "low", "approval_required": False,
                              "tools_used": ["calculator"]})
    # finish appends a "finish" event.
    assert tr.events[-1][1] == "finish"
    assert tr.duration_ms > 0


def test_recent_traces_capped():
    clear_traces()
    for i in range(300):
        new_trace(session_id=f"s{i}")
    traces = recent_traces()
    assert len(traces) <= 256


def test_clear_traces_empties_buffer():
    clear_traces()
    new_trace(session_id="x")
    new_trace(session_id="y")
    assert len(recent_traces()) == 2
    clear_traces()
    assert recent_traces() == []


def test_finish_trace_populates_phase6_fields():
    clear_traces()
    tr = new_trace(session_id="s1")
    finish_trace(
        tr,
        result={
            "intent": "complex",
            "complexity": "difficult",
            "selected_path": "complex",
            "selected_model": "openai/gpt-5.5",
            "gpu_policy": "prefer_gpu",
            "processor_split": "Partial CPU/GPU",
            "estimated_cost_usd": 0.0123,
            "cloud_used": True,
            "fallback_used": False,
        },
    )
    d = tr.to_dict()
    assert d["gpu_policy"] == "prefer_gpu"
    assert d["processor_split"] == "Partial CPU/GPU"
    assert d["estimated_cost_usd"] == 0.0123
    assert d["cloud_used"] is True


def test_trace_to_dict_defaults_for_local():
    clear_traces()
    tr = new_trace(session_id="s1")
    d = tr.to_dict()
    assert d["gpu_policy"] is None
    assert d["processor_split"] is None
    assert d["estimated_cost_usd"] == 0.0
    assert d["cloud_used"] is False


def test_trace_retention_honors_config(monkeypatch):
    monkeypatch.setattr(settings, "trace_retention_limit", 10)
    clear_traces()
    for i in range(25):
        new_trace(session_id=f"s{i}")
    traces = recent_traces()
    assert len(traces) <= 10
    assert traces[0].session_id == "s15"
    monkeypatch.setattr(settings, "trace_retention_limit", 256)


def test_trace_retention_rejects_zero(monkeypatch):
    from jarvis.observability import trace as trace_mod

    monkeypatch.setattr(settings, "trace_retention_limit", 0)
    assert trace_mod._ring_maxlen() == trace_mod._DEFAULT_MAX_TRACES
    monkeypatch.setattr(settings, "trace_retention_limit", 256)
