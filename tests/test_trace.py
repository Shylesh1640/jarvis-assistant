"""Tests for the per-request trace module."""
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
