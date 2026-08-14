"""Tests for graph-state JSON codec (durable approval snapshots)."""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from jarvis.persistence.state_codec import state_from_json, state_to_json


def test_round_trip_messages_preserved():
    messages = [
        HumanMessage(content="do it"),
        AIMessage(
            content="",
            tool_calls=[{"name": "write_file", "args": {"file_path": "a.txt", "content": "x"}, "id": "c1", "type": "tool_call"}],
        ),
        ToolMessage(content="Wrote a.txt", tool_call_id="c1", name="write_file"),
    ]
    state = {
        "user_input": "write a file",
        "messages": messages,
        "intent": "coding",
        "approval_required": True,
        "some_list": [{"a": 1}],
        "a_bool": True,
        "a_none": None,
    }
    encoded = state_to_json(state)
    decoded = state_from_json(encoded)

    # Primitives survive verbatim.
    assert decoded["user_input"] == "write a file"
    assert decoded["intent"] == "coding"
    assert decoded["approval_required"] is True
    assert decoded["some_list"] == [{"a": 1}]

    # Messages are real LangChain message objects again with tool calls intact.
    msgs = decoded["messages"]
    assert len(msgs) == 3
    assert isinstance(msgs[0], HumanMessage)
    assert isinstance(msgs[1], AIMessage)
    assert msgs[1].tool_calls == [
        {"name": "write_file", "args": {"file_path": "a.txt", "content": "x"}, "id": "c1", "type": "tool_call"}
    ]
    assert isinstance(msgs[2], ToolMessage)
    assert msgs[2].content == "Wrote a.txt"


def test_state_to_json_never_raises_on_exotic_values():
    class Exotic:
        def __str__(self):
            return "<exotic>"

    state = {"messages": [], "odd": [{"x": Exotic()}], "key": {"nested": [1, "two"]}}
    encoded = state_to_json(state)
    assert encoded["odd"][0]["x"] == "<exotic>"
    assert encoded["key"]["nested"] == [1, "two"]


def test_state_from_json_tolerates_garbage():
    decoded = state_from_json({"messages": [{"type": "text", "text": "fallback"}]})
    assert decoded["messages"] == ["fallback"]
    assert state_from_json(None) == {}  # type: ignore[arg-type]