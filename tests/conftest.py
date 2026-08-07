"""Shared pytest fixtures for the Jarvis test suite."""
from __future__ import annotations

from types import SimpleNamespace

import pytest


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


@pytest.fixture
def fake_ollama():
    """Expose the captured ChatOllama instances for direct assertions."""
    return _FakeChatOllama
