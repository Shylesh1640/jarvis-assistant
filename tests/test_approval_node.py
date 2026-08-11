"""Tests for the approval node: risk classification, pending capture, TTL."""
from __future__ import annotations

import datetime

from langchain_core.messages import AIMessage, HumanMessage

from jarvis.orchestration.approval_node import (
    approval_gate,
    approval_is_expired,
    check_risk,
)


def _tc(name: str, args: dict, call_id: str = "c1") -> dict:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def _state_with_tool_call(*tool_calls):
    return {
        "messages": [
            HumanMessage(content="do it"),
            AIMessage(content="", tool_calls=list(tool_calls)),
        ],
    }


# ---------------------------------------------------------------------------
# check_risk
# ---------------------------------------------------------------------------


def test_check_risk_gates_medium_tool_and_captures_pending():
    state = _state_with_tool_call(_tc("write_file", {"file_path": "a.txt"}))
    check_risk(state)
    assert state["approval_required"] is True
    assert state["risk_level"] == "medium"
    assert state["pending_action"] is not None
    assert "write_file" in state["pending_action"]


def test_check_risk_low_tool_is_auto_allowed():
    state = _state_with_tool_call(_tc("calculator", {"expression": "2+2"}))
    check_risk(state)
    assert state["approval_required"] is False
    assert state["risk_level"] == "low"


def test_check_risk_no_tool_calls_is_low():
    state = {"messages": [HumanMessage(content="hi")]}
    check_risk(state)
    assert state["approval_required"] is False
    assert state["risk_level"] == "low"


def test_check_risk_approved_resume_passes_through():
    state = _state_with_tool_call(_tc("write_file", {"file_path": "a.txt"}))
    state["approved"] = True
    check_risk(state)
    assert state["approval_required"] is False
    assert state["risk_level"] == "low"
    assert state["approved"] is False  # consumed


# ---------------------------------------------------------------------------
# approval_gate
# ---------------------------------------------------------------------------


def test_approval_gate_stores_pending_calls_id_and_expiry(monkeypatch):
    from jarvis.orchestration import approval_node as an

    monkeypatch.setattr(an.settings, "approval_ttl_seconds", 120, raising=True)
    state = _state_with_tool_call(_tc("write_file", {"file_path": "a.txt"}))
    state["pending_action"] = "write_file(file_path='a.txt')"
    approval_gate(state)

    assert state["pending_tool_calls"] == [
        {"name": "write_file", "args": {"file_path": "a.txt"}}
    ]
    assert state["approval_id"] and len(state["approval_id"]) == 32
    expires = datetime.datetime.fromisoformat(state["approval_expires_at"])
    delta = expires - datetime.datetime.now(datetime.timezone.utc)
    assert 110 < delta.total_seconds() <= 120
    assert "expires in 120s" in state["final_response"]


# ---------------------------------------------------------------------------
# expiry
# ---------------------------------------------------------------------------


def test_approval_is_expired_past_timestamp():
    past = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)
    ).isoformat()
    assert approval_is_expired({"approval_expires_at": past}) is True


def test_approval_is_not_expired_future_or_missing():
    future = (
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5)
    ).isoformat()
    assert approval_is_expired({"approval_expires_at": future}) is False
    assert approval_is_expired({}) is False
    assert approval_is_expired({"approval_expires_at": "not-a-date"}) is False