"""Shared pytest fixtures for the Jarvis test suite."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

# Shared tool-call queue consumed by _ScriptedChatOllama.invoke.
_TOOL_SCRIPT: list[dict] = []


class _FakeChatOllama:
    """Stand-in for langchain_ollama.ChatOllama used by branches.

    Records the model name on each instantiation so tests can assert which
    model was selected. `.bind_tools(...)` returns self so the branch's
    chained call still works. `.invoke(...)` returns a fake response that
    mimics a LangChain AIMessage (has `.content` and `.tool_calls`).
    """

    instances: list["_FakeChatOllama"] = []

    def __init__(self, model: str = "", base_url: str = "", temperature: float = 0.4, **kwargs) -> None:
        self.model = model
        self.base_url = base_url
        self.temperature = temperature
        # Store any runtime options passed by ollama_client._build (num_ctx,
        # keep_alive, etc.) so tests can assert them without a real ChatOllama.
        self.options = kwargs
        self.num_ctx = kwargs.get("num_ctx")
        self.keep_alive = kwargs.get("keep_alive")
        self.num_gpu = kwargs.get("num_gpu")
        self.num_predict = kwargs.get("num_predict")
        self.bound_tools: list = []
        self.last_prompt: object | None = None
        _FakeChatOllama.instances.append(self)

    def bind_tools(self, tools: list):
        self.bound_tools = list(tools)
        return self

    def invoke(self, prompt, **kwargs):
        # Record whatever was passed in so tests can assert prompt framing.
        self.last_prompt = prompt
        # Return a fake response object. Branches only read .content and
        # .tool_calls, both of which are present here.
        return SimpleNamespace(content=f"[fake response from {self.model}]", tool_calls=[])


@pytest.fixture(autouse=True)
def _patch_chat_ollama(monkeypatch):
    """Replace ChatOllama with _FakeChatOllama for every test."""
    _FakeChatOllama.instances.clear()
    import langchain_ollama

    monkeypatch.setattr(langchain_ollama, "ChatOllama", _FakeChatOllama)
    # `from langchain_ollama import ChatOllama` in ollama_client.py binds the
    # name at import time in that module's namespace, so patch there too.
    import jarvis.models.ollama_client as oc

    monkeypatch.setattr(oc, "ChatOllama", _FakeChatOllama)
    yield
    _FakeChatOllama.instances.clear()


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Restore the module-global rate limiter between tests.

    Tests that monkeypatch ``settings.rate_limit_per_minute`` and call
    ``reload_limiter()`` would otherwise leave a tiny or zero limit in place
    and throttle unrelated tests in the same process.
    """
    from jarvis.security import ratelimit as ratelimit_module

    ratelimit_module.reload_limiter()
    yield
    ratelimit_module.reload_limiter()


@pytest.fixture
def fake_ollama():
    """Expose the captured ChatOllama instances for direct assertions."""
    return _FakeChatOllama


class _ScriptedChatOllama:
    """Like _FakeChatOllama but returns real AIMessage (with tool_calls).

    Tool-call dicts are consumed from a module-level queue shared across
    instances so tests can script instances created deep inside
    ``get_model_named``. Once the queue drains, invokes return a plain final
    AIMessage. Used to exercise the tool loop through the real LangGraph.
    """

    instances: list["_ScriptedChatOllama"] = []

    def __init__(self, model: str = "", base_url: str = "", temperature: float = 0.4, **kwargs) -> None:
        self.model = model
        self.options = kwargs
        self.bound_tools: list = []
        self.last_prompt: object | None = None
        self.invocation_count: int = 0
        _ScriptedChatOllama.instances.append(self)

    def bind_tools(self, tools: list):
        self.bound_tools = list(tools)
        return self

    def invoke(self, prompt, **kwargs):
        self.last_prompt = prompt
        self.invocation_count += 1
        if _TOOL_SCRIPT:
            tc = _TOOL_SCRIPT.pop(0)
            return AIMessage(content="", tool_calls=[tc])
        return AIMessage(content="all done", tool_calls=[])


@pytest.fixture
def monologue_ollama(monkeypatch):
    """Patch ChatOllama with the scripted fake returning real AIMessages."""
    _ScriptedChatOllama.instances.clear()
    _TOOL_SCRIPT.clear()

    import langchain_ollama
    import jarvis.models.ollama_client as oc

    monkeypatch.setattr(langchain_ollama, "ChatOllama", _ScriptedChatOllama)
    monkeypatch.setattr(oc, "ChatOllama", _ScriptedChatOllama)
    yield _ScriptedChatOllama
    _TOOL_SCRIPT.clear()
    _ScriptedChatOllama.instances.clear()


@pytest.fixture
def tool_script():
    """Return the shared queue of pending tool-call dicts to script."""
    return _TOOL_SCRIPT
