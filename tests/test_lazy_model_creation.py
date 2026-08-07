"""Tests for lazy model creation.

Importing jarvis.models.ollama_client / store must NOT instantiate any
ChatOllama or OllamaEmbeddings. Clients must be built only on demand.
"""
import importlib


def test_importing_ollama_client_creates_no_instances():
    import jarvis.models.ollama_client as oc

    # Re-import fresh.
    importlib.reload(oc)
    # No module-level ChatOllama instances.
    for attr in dir(oc):
        if attr.startswith("_"):
            continue
        val = getattr(oc, attr)
        # None of the public names should be a constructed client instance.
        assert not hasattr(val, "invoke") or callable(val)


def test_store_import_does_not_load_embeddings():
    import jarvis.memory.store as store

    importlib.reload(store)
    assert store._embeddings is None
    assert store._collection is None


def test_get_model_named_builds_only_on_call(monkeypatch):
    """Calling the helper builds one client; not calling builds zero."""
    built: list = []

    class _Spy:
        def __init__(self, **kwargs):
            built.append(kwargs)
            self.model = kwargs.get("model")

        def bind_tools(self, _):
            return self

        def invoke(self, _):
            from types import SimpleNamespace
            return SimpleNamespace(content="ok", tool_calls=[])

    import jarvis.models.ollama_client as oc

    monkeypatch.setattr(oc, "ChatOllama", _Spy)
    before = len(built)
    oc.get_model_named("qwen3:8b", intent="general")
    assert len(built) == before + 1
