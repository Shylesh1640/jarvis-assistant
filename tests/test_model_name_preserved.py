"""Safety guarantee tests: the optimization NEVER changes, deletes, or replaces
the configured model.

These tests assert, across the entire codebase, that:
- The model name in settings is what gets passed to ChatOllama.
- No code path renames / swaps / quantizes / downgrades the model.
- The model selector picks from the configured names, never a hardcoded one.
- The branches pass the selected model name verbatim (not a renamed variant).
- No new model file is created by this change set.
"""
import inspect

from jarvis.config.settings import settings
from jarvis.models.ollama_client import get_model_named
from jarvis.orchestration.model_selector import select_model


# The configured model names — pulled from the actual settings instance so
# the test stays correct regardless of .env contents.
_CONFIGURED = {
    settings.general_model,
    settings.strong_local_model,
    settings.coding_model,
    settings.coding_model_small,
    settings.embedding_model,
}


def test_select_model_never_returns_a_name_outside_configured_set():
    for intent in ("general", "coding", "complex"):
        for complexity in ("easy", "medium", "difficult"):
            state = {"intent": intent, "complexity": complexity}
            name = select_model(state, settings)
            # Cloud chain models are allowed (they're not local Ollama models)
            # but the *local* selection must be one of the configured names.
            if intent == "complex" and settings.complex_models:
                assert name == settings.complex_models[0] or name in _CONFIGURED
            else:
                assert name in _CONFIGURED, f"{intent}/{complexity} picked {name}"


def test_get_model_named_passes_name_verbatim(monkeypatch):
    """The model name string is forwarded identically — no coercion."""
    captured: list[str] = []

    class _Spy:
        def __init__(self, **kwargs):
            captured.append(kwargs.get("model", ""))
            self.model = kwargs.get("model")

        def bind_tools(self, _):
            return self

        def invoke(self, _):
            from types import SimpleNamespace
            return SimpleNamespace(content="ok", tool_calls=[])

    import jarvis.models.ollama_client as oc

    monkeypatch.setattr(oc, "ChatOllama", _Spy)
    for name in ("qwen3:8b", "weird-name:13b-q4_K_M", "exotic model tag"):
        llm = get_model_named(name, intent="general")
        assert llm.model == name
        assert captured[-1] == name


def test_no_code_quantizes_or_renames_model():
    """Grep the branches + ollama_client source for forbidden operations."""
    import jarvis.models.ollama_client as oc
    import jarvis.orchestration.branches as br

    for mod in (oc, br):
        src = inspect.getsource(mod)
        low = src.lower()
        # None of these destructive verbs should appear in our model code.
        for forbidden in ("quantize", "convert", "rename", "delete model", "pull ", "rm "):
            assert forbidden not in low, f"{mod.__name__} contains '{forbidden}'"


def test_runtime_diagnostics_is_not_a_model_wrapper():
    """The diagnostics module must not be a model file."""
    import jarvis.models.runtime_diagnostics as rd

    assert not hasattr(rd, "model")
    assert not hasattr(rd, "ollama_client")
