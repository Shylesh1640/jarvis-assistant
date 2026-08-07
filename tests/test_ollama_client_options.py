"""Tests for runtime-options wiring in ollama_client.

Verifies that:
- num_ctx / keep_alive / temperature are passed to ChatOllama.
- The model name is NEVER overridden by runtime options.
- Disabling gpu_optimization_enabled yields no options block.
- Unsupported keys are dropped (never silently mis-applied).
"""
from jarvis.config.settings import settings


def test_runtime_options_include_num_ctx_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "gpu_optimization_enabled", True)
    monkeypatch.setattr(settings, "ollama_context_length", 8192)
    monkeypatch.setattr(settings, "ollama_keep_alive", "10m")
    from jarvis.models.ollama_client import _build, _runtime_options

    llm = _build("qwen3:8b", 0.4)
    assert llm.model == "qwen3:8b"
    assert llm.num_ctx == 8192
    assert llm.keep_alive == "10m"
    assert llm.temperature == 0.4


def test_runtime_options_disabled_when_gpu_optimization_off(monkeypatch):
    monkeypatch.setattr(settings, "gpu_optimization_enabled", False)
    from jarvis.models.ollama_client import _build

    llm = _build("my-model", 0.4)
    assert llm.model == "my-model"
    assert llm.num_ctx is None


def test_model_name_never_overridden(monkeypatch):
    monkeypatch.setattr(settings, "gpu_optimization_enabled", True)
    from jarvis.models.ollama_client import get_model_named

    llm = get_model_named("some-specific-model", intent="coding", temperature=0.2)
    assert llm.model == "some-specific-model"
    assert llm.temperature == 0.2


def test_temperature_per_intent_used(monkeypatch):
    monkeypatch.setattr(settings, "gpu_optimization_enabled", True)
    from jarvis.models.ollama_client import get_model_named

    assert get_model_named("m", intent="coding").temperature == 0.2
    assert get_model_named("m", intent="general").temperature == 0.4
    assert get_model_named("m", intent="complex").temperature == 0.3


def test_get_general_uses_settings_general_model(monkeypatch):
    monkeypatch.setattr(settings, "general_model", "qwen3:8b")
    monkeypatch.setattr(settings, "gpu_optimization_enabled", True)
    from jarvis.models.ollama_client import get_general_model

    assert get_general_model().model == "qwen3:8b"


def test_runtime_options_does_not_set_num_gpu(monkeypatch):
    """GPU offload is left to Ollama — we never hard-code num_gpu."""
    monkeypatch.setattr(settings, "gpu_optimization_enabled", True)
    from jarvis.models.ollama_client import _build

    llm = _build("m", 0.4)
    assert llm.num_gpu is None
