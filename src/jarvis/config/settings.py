"""Central app settings loaded from environment variables."""
import logging

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ollama_base_url: str = "http://localhost:11434"
    general_model: str = "qwen3:8b"
    strong_local_model: str = "qwen3:14b"
    coding_model: str = "qwen3-coder:30b"
    coding_model_small: str = "qwen2.5-coder:7b"
    embedding_model: str = "qwen3-embedding:latest"

    # Whether to actually use the strong local model for medium/difficult
    # general tasks. When False, the general branch always uses general_model
    # (useful on hardware that cannot run qwen3:14b comfortably).
    use_strong_local: bool = True

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    complex_model_chain: str = "anthropic/claude-opus-4.1,openai/gpt-5.5,google/gemini-2.5-pro"

    app_env: str = "development"
    log_level: str = "INFO"
    vector_db_path: str = "./data/vector_store"

    # Folder scanned by the `jarvis ingest` CLI for .txt/.md documents to load
    # into the long-term RAG store.
    docs_folder: str = "./data/docs"

    # --- Coding toolset ---
    # Root directory the write/exec tools are allowed to operate inside. Any
    # path that escapes this root (via absolute paths or ``..``) is rejected
    # before the tool runs. Set to an empty string to disable the guard
    # (NOT recommended).
    workspace_dir: str = "./workspace"
    # Shell commands the run_shell tool will accept without further review.
    # Risk classification still applies; this only governs the allowlist.
    shell_allowed_commands: str = "ls,dir,cat,type,echo,git,pytest,python -m pytest,ruff,pip,uv,npm,npm run build,npm test"
    # Hard wall-clock cap for run_tests / run_shell invocations (seconds).
    tool_subprocess_timeout: int = 120

    # --- Persistence ---
    # Postgres DSN. When empty the app falls back to a local SQLite file at
    # ``sqlite_path`` so the assistant still works without Docker.
    postgres_dsn: str = ""
    sqlite_path: str = "./data/jarvis.db"
    # After how many (user, assistant) turns a conversation is summarized.
    summary_every_turns: int = 10

    # --- UI / request toggles ---
    # Default answer style emitted in the system prompt when the client
    # doesn't request one: "" | "concise" | "detailed" | "code". Empty
    # means "no style directive" so prompts remain unchanged unless a UI
    # toggle is on (preserving the original assistant behaviour).
    default_answer_style: str = ""
    # When True, the system prompt asks the model to include brief reasoning.
    default_show_reasoning: bool = False

    # --- Context-window management ---
    # Max number of (user, assistant) history turns to keep in the prompt.
    # Older turns are dropped before sending. 20 turns = up to 40 messages.
    history_max_turns: int = 20
    # Soft token-budget cap for the conversation-history block. The estimator
    # is a word-count proxy (words * 1.3). History is truncated from the
    # oldest end until the budget fits. RAG context + system prompt + the
    # current user message are *not* counted against this budget.
    context_token_budget: int = 12000
    # Default number of chunks retrieved from the RAG store.
    retrieval_top_k: int = 5
    # Hard cap (tokens, same word-count proxy) applied to the *retrieved RAG
    # context block* before it is injected into the prompt, so a large hit
    # cannot blow the model context window. 0 = unbounded (legacy).
    rag_context_token_cap: int = 2048
    # Hard cap (tokens) for the highlighted selected_text snippet, again so
    # a giant paste cannot dominate the context window. 0 = unbounded.
    selected_text_token_cap: int = 1024

    # --- GPU / Ollama runtime optimization (request-level options) ---
    # Master switch; when False the runtime-options block below is skipped
    # and Ollama uses its server defaults.
    gpu_optimization_enabled: bool = True
    # Context window size sent per request. Conservative default of 4096
    # unless your config explicitly raises it.
    ollama_context_length: int = 4096
    # Batch size for prompt processing per request.
    ollama_num_batch: int = 512
    # Flash attention toggle (1 on / 0 off). This is a request option in
    # modern Ollama; older builds may ignore it.
    ollama_flash_attention: int = 1
    # KV cache quantization type, e.g. "q8_0", "f16". "q8_0" halves KV
    # cache memory with negligible quality loss.
    ollama_kv_cache_type: str = "q8_0"
    # How long a model stays loaded in VRAM after the last request.
    # Accepts Ollama duration strings like "5m", "30s", "0".
    ollama_keep_alive: str = "5m"
    # Limit the number of parallel requests Ollama serves. NOTE: this is a
    # server-side setting (env var on `ollama serve`), not a request option.
    # We surface it here only for diagnostics/validation — the app itself
    # already serializes local generation (one active model at a time).
    ollama_num_parallel: int = 1
    # Maximum models Ollama keeps loaded simultaneously (server-side env var).
    ollama_max_loaded_models: int = 1

    @property
    def complex_models(self) -> list[str]:
        return [m.strip() for m in self.complex_model_chain.split(",") if m.strip()]

    def ollama_request_options(self) -> dict:
        """Build the request-level ``options`` dict for ChatOllama.

        Only keys actually supported by the installed langchain_ollama /
        Ollama version are emitted, so we never send unsupported params.
        Returns an empty dict when ``gpu_optimization_enabled`` is False.
        """
        if not self.gpu_optimization_enabled:
            return {}
        opts: dict = {"num_ctx": self.ollama_context_length}
        if self.ollama_num_batch > 0:
            opts["num_batch"] = self.ollama_num_batch
        if self.ollama_flash_attention in (0, 1):
            opts["flash_attention"] = bool(self.ollama_flash_attention)
        if self.ollama_kv_cache_type:
            opts["kv_cache_type"] = self.ollama_kv_cache_type
        if self.ollama_keep_alive:
            opts["keep_alive"] = self.ollama_keep_alive
        return _filter_supported_options(opts)


# ---------------------------------------------------------------------------
# Compat gate: only emit Ollama options the installed version understands.
# ---------------------------------------------------------------------------

def _filter_supported_options(opts: dict) -> dict:
    """Drop request options the installed Ollama build doesn't advertise.

    We can't easily introspect the Ollama server for *request* option support,
    so we allow-list keys known to be supported by Ollama >= 0.5 (which
    predates the minimum langchain_ollama in this project). Unknown keys
    pass through silently but we warn so the issue is visible.
    """
    known = {
        "num_ctx", "num_batch", "temperature", "top_p", "top_k",
        "num_predict", "stop", "seed", "keep_alive",
        "flash_attention", "kv_cache_type", "num_gpu", "num_thread",
        "main_gpu", "low_vram",
    }
    out: dict = {}
    for k, v in opts.items():
        if k in known:
            out[k] = v
        else:
            logger.warning("Dropping unsupported Ollama option '%s' (not in known set)", k)
    return out


def validate_runtime_settings(s: "Settings | None" = None) -> list[str]:
    """Return a list of human-readable warning strings for bad runtime config.

    Empty list = config is valid. Pure logic, no side effects — used by the
    startup validator and the /runtime diagnostics endpoint.
    """
    if s is None:
        s = settings
    warnings: list[str] = []
    if not s.ollama_base_url:
        warnings.append("OLLAMA_BASE_URL is empty.")
    if s.ollama_num_parallel < 1:
        warnings.append("OLLAMA_NUM_PARALLEL must be >= 1; using single request lane.")
    if s.ollama_max_loaded_models > s.ollama_num_parallel:
        warnings.append(
            "OLLAMA_MAX_LOADED_MODELS > OLLAMA_NUM_PARALLEL may load spare"
            " models that evict the active one; recommend setting both to 1."
        )
    if s.ollama_context_length < 512:
        warnings.append("OLLAMA_CONTEXT_LENGTH < 512 is very small and may truncate prompts.")
    if s.ollama_num_batch < 1:
        warnings.append("OLLAMA_NUM_BATCH must be >= 1.")
    if s.ollama_flash_attention not in (0, 1):
        warnings.append("OLLAMA_FLASH_ATTENTION should be 0 or 1.")
    if s.ollama_kv_cache_type and s.ollama_kv_cache_type not in {
        "q8_0", "q4_0", "q4_1", "f16", "f32", ""
    }:
        warnings.append(f"OLLAMA_KV_CACHE_TYPE='{s.ollama_kv_cache_type}' may not be supported.")
    if s.rag_context_token_cap < 0 or s.selected_text_token_cap < 0:
        warnings.append("RAG / selected-text caps must be >= 0 (0 = unbounded).")
    if s.history_max_turns < 1:
        warnings.append("HISTORY_MAX_TURNS < 1 disables history entirely.")
    return warnings


settings = Settings()
