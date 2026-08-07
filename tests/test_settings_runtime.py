"""Tests for the GPU/runtime settings validation."""
from jarvis.config.settings import Settings, _filter_supported_options, validate_runtime_settings


def test_defaults_are_conservative():
    s = Settings()
    assert s.ollama_num_parallel == 1
    assert s.ollama_max_loaded_models == 1
    assert s.ollama_context_length == 4096
    assert s.ollama_num_batch == 512
    assert s.ollama_keep_alive == "5m"
    assert s.ollama_kv_cache_type == "q8_0"
    assert s.ollama_flash_attention == 1
    assert s.gpu_optimization_enabled is True


def test_validate_runtime_settings_clean_defaults():
    s = Settings()
    assert validate_runtime_settings(s) == []


def test_validate_runtime_settings_warns_on_zero_parallel():
    s = Settings(ollama_num_parallel=0)
    warns = validate_runtime_settings(s)
    assert any("NUM_PARALLEL" in w for w in warns)


def test_validate_runtime_settings_warns_on_too_many_loaded_models():
    s = Settings(ollama_num_parallel=1, ollama_max_loaded_models=4)
    warns = validate_runtime_settings(s)
    assert any("MAX_LOADED_MODELS" in w for w in warns)


def test_validate_runtime_settings_warns_on_tiny_context():
    s = Settings(ollama_context_length=128)
    warns = validate_runtime_settings(s)
    assert any("CONTEXT_LENGTH" in w for w in warns)


def test_validate_runtime_settings_warns_on_bad_kv_cache():
    s = Settings(ollama_kv_cache_type="banana")
    warns = validate_runtime_settings(s)
    assert any("KV_CACHE_TYPE" in w for w in warns)


def test_validate_runtime_settings_warns_on_bad_flash_attention():
    s = Settings(ollama_flash_attention=7)
    warns = validate_runtime_settings(s)
    assert any("FLASH_ATTENTION" in w for w in warns)


def test_validate_runtime_settings_warns_on_negative_caps():
    s = Settings(rag_context_token_cap=-1, selected_text_token_cap=-5)
    warns = validate_runtime_settings(s)
    assert any("caps" in w for w in warns)


def test_validate_runtime_settings_warns_on_empty_base_url():
    s = Settings(ollama_base_url="")
    warns = validate_runtime_settings(s)
    assert any("OLLAMA_BASE_URL" in w for w in warns)


def test_filter_supported_options_drops_unknown_keys():
    out = _filter_supported_options({
        "num_ctx": 4096,            # known
        "bogus_option": 123,        # unknown -> dropped
        "temperature": 0.4,         # known
    })
    assert "num_ctx" in out
    assert "temperature" in out
    assert "bogus_option" not in out


def test_filter_supported_options_keeps_known_set():
    for k in ("num_ctx", "num_batch", "temperature", "keep_alive",
              "flash_attention", "kv_cache_type", "num_predict"):
        assert _filter_supported_options({k: 1}) == {k: 1}
