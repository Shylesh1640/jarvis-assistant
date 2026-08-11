"""Tests for the branch tool loops, bindings, and execution wiring (T3/T4).

Uses ``_ScriptedChatOllama`` (from conftest) which returns real AIMessage
objects with configurable tool calls, so we can drive the branch tool loop
and, via ``jarvis_graph``, the full ToolNode execution path.
"""
from __future__ import annotations

import pytest

from jarvis.config.settings import settings
from jarvis.guardrails.risk import check_tool_risk
from jarvis.orchestration.graph import jarvis_graph
from jarvis.orchestration.branches import run_coding_branch, run_general_branch
from jarvis.tools.registry import (
    CODING_BOUND_TOOLS,
    GENERAL_BOUND_TOOLS,
    all_tools,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pinned_settings(monkeypatch):
    for name in ("coding_model", "coding_model_small", "general_model", "strong_local_model"):
        monkeypatch.setattr(settings, name, getattr(settings, name), raising=True)
    return settings


def _state(user_input: str, **extra) -> dict:
    state = {
        "user_input": user_input,
        "history": [],
        "fallback_count": 0,
        "messages": [],
        "show_reasoning": False,
        "answer_style": "",
        "selected_text": "",
        "as_background_task": False,
    }
    state.update(extra)
    return state


def _tool_call(name: str, args: dict, call_id: str = "call_1") -> dict:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def _total_invocations(cls) -> int:
    """The graph creates one ChatOllama instance per LLM round."""
    return sum(inst.invocation_count for inst in cls.instances)


# ---------------------------------------------------------------------------
# Bindings come from the registry and are unique
# ---------------------------------------------------------------------------


def test_registry_all_tools_are_unique():
    names = [t.name for t in all_tools()]
    assert len(names) == len(set(names))


def test_general_branch_binds_all_general_tools(monologue_ollama):
    run_general_branch(_state("say hi", intent="general", complexity="easy"))
    instance = monologue_ollama.instances[-1]
    bound = {t.name for t in instance.bound_tools}
    assert bound == {t.name for t in GENERAL_BOUND_TOOLS}


def test_coding_branch_binds_all_coding_tools(monologue_ollama):
    run_coding_branch(_state("write a function", intent="coding", complexity="easy"))
    instance = monologue_ollama.instances[-1]
    bound = {t.name for t in instance.bound_tools}
    assert bound == {t.name for t in CODING_BOUND_TOOLS}


# ---------------------------------------------------------------------------
# Branch-level tool loop: a tool call increments the cap and appends the
# AIMessage; the graph then executes tools and routes back to the branch.
# ---------------------------------------------------------------------------


def _invoke_graph(user_input: str, session_id: str, *, thread_id: str | None = None, **extra) -> dict:
    flat = _state(user_input, session_id=session_id, **extra)
    return jarvis_graph.invoke(
        flat,
        config={"configurable": {"thread_id": thread_id or f"t-{session_id}"}},
    )


def test_tool_loop_runs_calculator_and_finishes(monologue_ollama, pinned_settings, tool_script):
    tool_script.append(_tool_call("calculator", {"expression": "2 + 2"}))
    result = _invoke_graph("what is 2 + 2?", "loopcalc")
    assert result["final_response"] == "all done"
    assert "calculator" in result["tools_used"]
    # The ToolNode executed the calculator and recorded the result.
    assert any(r["name"] == "calculator" and "4" in r["content"] for r in result["tool_results"])
    # Exactly one extra LLM round happened for the final answer.
    assert _total_invocations(monologue_ollama) == 2


def test_tool_loop_stops_at_cap(monologue_ollama, tool_script, monkeypatch):
    monkeypatch.setattr(settings, "max_tool_iterations", 2, raising=True)
    for i in range(2):
        tool_script.append(_tool_call("calculator", {"expression": f"1 + {i}"}, call_id=f"c{i}"))
    result = _invoke_graph("add some numbers", "loopcap")
    # Two tool rounds were executed, then the guard stopped the loop without
    # asking the LLM for a third answer.
    assert result["final_response"].startswith("I stopped after reaching")
    assert _total_invocations(monologue_ollama) == 2


def test_read_file_is_low_risk():
    assert check_tool_risk("read_file", {"file_path": "src/a.py"}) == "low"


def test_write_file_is_gated():
    assert check_tool_risk("write_file", {"file_path": "a.txt"}) == "medium"


# ---------------------------------------------------------------------------
# E2E approval resume: a written tool pauses for approval, then the stored
# call executes exactly on the approved resume.
# ---------------------------------------------------------------------------


def test_graph_pauses_for_approval_then_executes_on_resume(
    monologue_ollama, pinned_settings, tool_script, monkeypatch, tmp_path
):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr("jarvis.tools.coding.paths.settings.workspace_dir", str(ws))

    thread = "t-approve-e2e"
    tool_script.append(_tool_call("write_file", {"file_path": "out.txt", "content": "hello"}))

    # Round 1: the branch requests write_file -> medium risk -> approval gate.
    first = _invoke_graph("write a file for me", "approve", thread_id=thread)
    assert first["approval_required"] is True
    assert "write_file(out.txt", first["pending_action"] or ""
    assert first["pending_tool_calls"] == [
        {"name": "write_file", "args": {"file_path": "out.txt", "content": "hello"}}
    ]
    assert first["approval_id"]
    assert first["approval_expires_at"]
    assert (ws / "out.txt").exists() is False  # nothing executed yet

    # Round 2: user approves -> exactly the stored call runs, then final answer.
    resume_state = dict(first)
    resume_state["approved"] = True
    second = jarvis_graph.invoke(
        resume_state,
        config={"configurable": {"thread_id": thread}},
    )
    assert second["approval_required"] is False
    assert second["final_response"] == "all done"
    assert second["tools_used"] == ["write_file"]
    result_files: list[str] = [
        str(r["content"]) for r in second["tool_results"] if r["name"] == "write_file"
    ]
    assert result_files and "Wrote" in result_files[0]
    assert (ws / "out.txt").read_text(encoding="utf-8") == "hello"


# ---------------------------------------------------------------------------
# Cap guard inside the branch (no LLM call when already at cap)
# ---------------------------------------------------------------------------


def test_branch_returns_prompt_when_at_cap(monologue_ollama, monkeypatch):
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    monkeypatch.setattr(settings, "max_tool_iterations", 3, raising=True)
    state = _state("keep going", intent="coding", complexity="easy")
    ai = AIMessage(
        content="",
        tool_calls=[_tool_call("list_directory", {"path": "."}, call_id="c1")],
    )
    tm = ToolMessage(content="['a.py']", tool_call_id="c1", name="list_directory")
    state["messages"] = [HumanMessage(content="x"), ai, tm]
    state["tool_call_count"] = 3

    result = run_coding_branch(state)
    assert result["final_response"].startswith("I stopped after reaching")
    # Guard short-circuits before any model instance is created.
    assert len(monologue_ollama.instances) == 0
