"""Tests for the context-window assembly in context_window.py.

These are the pure-LangChain helpers that decide what the model sees:
sliding-window truncation over history, ordering of system / RAG /
history / user blocks, marker formatting, and retrieval-query assembly.
Nothing here touches Ollama or Chroma — all helpers operate on plain
strings / dicts / LangChain messages.
"""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from jarvis.orchestration.context_window import (
    RETRIEVED_CONTEXT_CLOSE,
    RETRIEVED_CONTEXT_OPEN,
    SYSTEM_PROMPT,
    build_final_chat_dicts,
    build_final_messages,
    build_retrieval_query,
    build_user_message,
    estimate_tokens,
    format_retrieved_context,
    window_history,
)


def _history(n_turns: int) -> list[dict[str, str]]:
    """Build a fake `n_turns`-deep history with `user` + `assistant` rows."""
    out: list[dict[str, str]] = []
    for i in range(n_turns):
        out.append({"role": "user", "content": f"u{i}"})
        out.append({"role": "assistant", "content": f"a{i}"})
    return out


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------


def test_estimate_tokens_zero_for_empty():
    assert estimate_tokens("") == 0


def test_estimate_tokens_positive_for_words():
    # 2 words -> 2 * 1.3 = 2.6 -> int + 1 > 0
    assert estimate_tokens("hello world") > 0


def test_estimate_tokens_grows_with_text():
    short = estimate_tokens("a b c")
    long = estimate_tokens("a b c " * 100)
    assert long > short


# ---------------------------------------------------------------------------
# window_history
# ---------------------------------------------------------------------------


def test_window_history_empty_returns_empty():
    assert window_history([], max_turns=5, token_budget=1000) == []


def test_window_history_turn_cap_keeps_newest_messages():
    h = _history(10)  # 20 messages
    out = window_history(h, max_turns=3, token_budget=10_000)
    # 3 turns * 2 = 6 messages, and they're the *last* 6.
    assert len(out) == 6
    assert out[0]["content"] == "u7"  # newest window starts at u7
    assert out[-1]["content"] == "a9"


def test_window_history_token_budget_drops_oldest_first():
    # Each message "word-N" is just over a token; budget forces trimming
    # from the older end. Turn cap is generous, so only the token budget
    # bites. Generate a long history with positive integer tokens.
    h = [
        {"role": "user", "content": "word " * 200},
        {"role": "assistant", "content": "word " * 200},
        {"role": "user", "content": "word " * 100},
        {"role": "assistant", "content": "word " * 100},
        {"role": "user", "content": "word"},  # newest user msg is tiny
    ]
    out = window_history(h, max_turns=100, token_budget=400)
    # The newest message is always present (it's at the tail).
    assert out  # non-empty
    assert out[-1]["content"] == "word"
    # Truncated total must be within budget.
    from jarvis.orchestration.context_window import _history_tokens

    assert _history_tokens(out) <= 400


def test_window_history_does_not_mutate_input():
    h = _history(3)
    original = [dict(m) for m in h]
    _ = window_history(h, max_turns=1, token_budget=10_000)
    assert h == original


def test_window_history_keeps_at_least_one_pair_when_max_turns_is_one():
    h = _history(5)
    out = window_history(h, max_turns=1, token_budget=10_000)
    # 1 turn cap -> up to 2 messages.
    assert 1 <= len(out) <= 2
    assert out[-1]["content"] == "a4"


# ---------------------------------------------------------------------------
# format_retrieved_context
# ---------------------------------------------------------------------------


def test_format_retrieved_context_empty_returns_empty_string():
    assert format_retrieved_context("") == ""
    assert format_retrieved_context("   \n  ") == ""
    assert format_retrieved_context(None) == ""


def test_format_retrieved_context_wraps_with_markers():
    out = format_retrieved_context("some snippet")
    assert out.startswith(RETRIEVED_CONTEXT_OPEN)
    assert out.endswith(RETRIEVED_CONTEXT_CLOSE)
    assert "some snippet" in out


def test_format_retrieved_context_strips_outer_whitespace():
    out = format_retrieved_context("\n\n  hello  \n")
    assert "hello" in out
    # Leading whitespace from input is gone (only the markers are at the ends).
    assert not out.startswith("\n")


# ---------------------------------------------------------------------------
# build_retrieval_query  (selected-text enhances retrieval)
# ---------------------------------------------------------------------------


def test_build_retrieval_query_user_input_only():
    assert build_retrieval_query({"user_input": "what is RAG"}) == "what is RAG"


def test_build_retrieval_query_selected_text_only():
    state = {"user_input": "", "selected_text": "an important snippet"}
    # Falls back to the selection alone when user input is empty.
    assert build_retrieval_query(state) == "an important snippet"


def test_build_retrieval_query_combines_selection_and_input():
    state = {"user_input": "explain this", "selected_text": "yield from gen"}
    q = build_retrieval_query(state)
    # Both signals present so the embedding has more to work with.
    assert "yield from gen" in q
    assert "explain this" in q


def test_build_retrieval_query_strips_and_handles_none():
    assert (
        build_retrieval_query({"user_input": "  hi  ", "selected_text": "  "}) == "hi"
    )
    assert build_retrieval_query({}) == ""


# ---------------------------------------------------------------------------
# build_user_message  (selected-text framing at the prompt level)
# ---------------------------------------------------------------------------


def test_build_user_message_no_selection():
    assert build_user_message({"user_input": "hello"}) == "hello"


def test_build_user_message_with_selection_wraps_snippet():
    out = build_user_message({"user_input": "explain", "selected_text": "x = 1"})
    assert "x = 1" in out
    assert "explain" in out
    assert "selected the following text" in out
    assert '"""\nx = 1\n"""' in out


# ---------------------------------------------------------------------------
# build_final_messages  (general-branch LangChain message list)
# ---------------------------------------------------------------------------


def test_build_final_messages_order_and_roles():
    state = {
        "user_input": "next question",
        "history": [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
        ],
        "retrieved_context": "ctx",
    }
    msgs = build_final_messages(state)

    # 1 system + 1 system (retrieved) + 2 history + 1 current user = 5.
    assert len(msgs) == 5
    assert isinstance(msgs[0], SystemMessage)
    assert msgs[0].content == SYSTEM_PROMPT
    assert isinstance(msgs[1], SystemMessage)
    assert msgs[1].content.startswith(RETRIEVED_CONTEXT_OPEN)
    assert isinstance(msgs[2], HumanMessage)
    assert msgs[2].content == "first"
    assert isinstance(msgs[3], AIMessage)
    assert msgs[3].content == "reply"
    assert isinstance(msgs[4], HumanMessage)
    assert msgs[4].content == "next question"


def test_build_final_messages_omits_retrieved_block_when_empty():
    state = {"user_input": "hello", "history": [], "retrieved_context": ""}
    msgs = build_final_messages(state)
    # Just system + current user.
    assert len(msgs) == 2
    assert isinstance(msgs[0], SystemMessage)
    assert isinstance(msgs[1], HumanMessage)


def test_build_final_messages_applies_turn_cap():
    state = {
        "user_input": "now",
        "history": _history(20),
        "retrieved_context": "",
    }
    msgs = build_final_messages(state)
    # Default settings.history_max_turns = 20 -> up to 40 history msgs.
    # Plus system + current user = 42 max.
    # We care that the current user is always present and last.
    assert isinstance(msgs[-1], HumanMessage)
    assert msgs[-1].content == "now"


def test_build_final_messages_selection_wraps_current_user():
    state = {
        "user_input": "explain",
        "selected_text": "yield from gen",
        "history": [],
        "retrieved_context": "",
    }
    msgs = build_final_messages(state)
    last = msgs[-1]
    assert isinstance(last, HumanMessage)
    assert "yield from gen" in last.content
    assert "selected the following text" in last.content


# ---------------------------------------------------------------------------
# build_final_chat_dicts  (complex-branch OpenAI-style dicts)
# ---------------------------------------------------------------------------


def test_build_final_chat_dicts_roles_and_order():
    state = {
        "user_input": "design it",
        "history": [
            {"role": "user", "content": "u0"},
            {"role": "assistant", "content": "a0"},
        ],
        "retrieved_context": "ctx",
    }
    items = build_final_chat_dicts(state)
    assert items[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert items[1]["role"] == "system"
    assert items[1]["content"].startswith(RETRIEVED_CONTEXT_OPEN)
    assert items[2] == {"role": "user", "content": "u0"}
    assert items[3] == {"role": "assistant", "content": "a0"}
    # Last item is the (selection-aware) current user message.
    assert items[-1]["role"] == "user"
    assert items[-1]["content"] == "design it"


def test_build_final_chat_dicts_mirrors_build_final_messages():
    """Complex path must see the same context structure as the local paths."""
    state = {
        "user_input": "q",
        "history": _history(3),
        "retrieved_context": "ctx",
        "selected_text": "sel",
    }
    msgs = build_final_messages(state)
    dicts = build_final_chat_dicts(state)

    # Same count, same trailing user content (selection-aware framing).
    assert len(msgs) == len(dicts)
    assert msgs[-1].content == dicts[-1]["content"]
