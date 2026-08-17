"""Central app settings loaded from environment variables."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ollama_base_url: str = "http://localhost:11434"
    general_model: str = "qwen3:8b"  # default Q4_K_M quantization
    strong_local_model: str = "qwen3:14b"  # default Q4_K_M quantization (medium/difficult general)
    coding_model: str = "qwen2.5-coder:7b-q5_K_M"  # 5-bit quantization to preserve syntax integrity
    coding_model_small: str = "qwen2.5-coder:7b-q5_K_M"  # same 5-bit coder for easy coding tasks
    embedding_model: str = "qwen3-embedding:latest"
    # Small model used by the intent router to classify borderline prompts
    # (JSON mode). Empty = reuse general_model. Fired only when the rules
    # heuristic is indecisive (medium-length prompt, no decisive keyword).
    router_model: str = ""
    # Master switch for the router LLM. When False, classification is pure
    # rules (latency stays predictable; borderline prompts fall to "general").
    router_llm_enabled: bool = True

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
    # Commands must START with an allowlisted entry (token-prefix match), so
    # "python -m pytest" only grants `python -m pytest ...`, not any `python`.
    # Risk classification still applies; this only governs the allowlist.
    shell_allowed_commands: str = "ls,dir,cat,type,echo,git,pytest,python -m pytest,ruff,pip,uv,npm,npm run build,npm test"
    # Hard wall-clock cap for run_tests / run_shell invocations (seconds).
    tool_subprocess_timeout: int = 120
    # Maximum file SIZE (bytes) the read_file tool will read. Larger files are
    # refused outright so read_file cannot pull an enormous file into memory.
    max_read_file_bytes: int = 1_000_000
    # Maximum number of CHARACTERS of file content returned to the model.
    # Larger outputs are truncated with a marker instead of flooding context.
    max_read_file_chars: int = 100_000
    # Maximum LLM tool-loop rounds per turn for the general and coding
    # branches. When reached the graph stops and returns a clear message.
    max_tool_iterations: int = 5
    # How long a pending approval stays valid before it expires (seconds).
    approval_ttl_seconds: int = 600

    # --- Runtime mode ---
    # Which runtime the assistant runs in. One of:
    #   local  = lightweight local runtime (SQLite + embedded vector store,
    #            tasks in-process). No Docker required. This is the default
    #            and is fully supported by the codebase.
    #   docker = Docker-backed persistence (Postgres) / containerized
    #            deployment. Adds optional Postgres; requires POSTGRES_DSN.
    #   auto   = prefer local; use Docker-backed services only when they are
    #            actually configured and reachable.
    # This setting is advisory for diagnostics/validation — the effective
    # backend still follows POSTGRES_DSN (empty => SQLite) for behaviour
    # compatibility.
    runtime_mode: str = "local"

    # --- Persistence ---
    # Postgres DSN. When empty the app falls back to a local SQLite file at
    # ``sqlite_path`` so the assistant still works without Docker.
    postgres_dsn: str = ""
    sqlite_path: str = "./data/jarvis.db"
    # After how many (user, assistant) turns a conversation is summarized.
    summary_every_turns: int = 10
    # Inactive sessions (no activity for this many days) are deleted by the
    # periodic maintenance sweep. 0 disables session cleanup.
    session_ttl_days: int = 7
    # Expired approval rows are hard-deleted after this retention in *hours*
    # (a TTL sweep flips them to ``expired``; older rows are physically
    # removed to keep the table bounded). 0 deletes them on the next sweep.
    expired_approval_retention_hours: int = 24
    # How often the periodic maintenance sweep runs (seconds). 0 disables
    # the sweep thread (startup-only cleanup).
    maintenance_sweep_interval: int = 300

    # --- Error handling / retry ---
    # Retries for *transient* Ollama failures (server down mid-flight,
    # request timeouts). Persistent errors (model missing, OOM) do NOT count
    # as retryable and surface immediately. 1 = no retries.
    retry_max_attempts: int = 3
    # Base sleep between retries; each attempt backs off linearly
    # (backoff, 2*backoff, ...). Seconds.
    retry_backoff_seconds: float = 1.0
    # When an OOM happens with full GPU offload, retry once with num_gpu=0
    # (CPU) instead of failing the request. The response warns the user.
    gpu_fallback_to_cpu: bool = True

    # --- Security ---
    # Requests per minute allowed from a single session (or client IP).
    # 0 disables rate limiting. Kept generous by default so interactive
    # usage is never throttled; tune per deployment.
    rate_limit_per_minute: int = 300
    # When True, /chat and /tasks require a valid per-session token (issued
    # via GET /sessions/{session_id}/token) and reject cross-session access.
    require_session_token: bool = False
    # Emit JSON-formatted logs for easy parsing. Disabled by default so the
    # dev console stays human-readable.
    json_logs_enabled: bool = False

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
    # Relevance gate for the *automatic* RAG injection in build_context.
    # Chroma reports cosine *distance* (0 = identical, ~1 = unrelated). Only
    # chunks closer than this distance are injected, so a question unrelated
    # to the stored docs gets NO context instead of an irrelevant chunk the
    # model might answer about. 0 disables the gate (legacy behaviour).
    # qwen3-embedding calibration: relevant ~0.05-0.35, irrelevant ~0.6+,
    # so 0.5 cleanly separates them.
    rag_relevance_threshold: float = 0.5
    # Weight (0..1) for the keyword-BM25 layer in hybrid retrieval. 0 = pure
    # vector similarity; 1 = pure keyword. 0.25 keeps semantic primacy but
    # boosts chunks with exact-term matches.
    rerank_keyword_weight: float = 0.25
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
    # GPU offload: number of layers to place on the GPU. -1 means "offload
    # EVERY layer", forcing 100% GPU execution so models never spill into
    # system RAM. If the whole model cannot fit in VRAM, Ollama errors out
    # instead of silently falling back to CPU. Set to 0 only to disable
    # GPU (not recommended).
    ollama_num_gpu: int = -1
    # Context window size sent per request. Raised to 8192 so the sliding
    # history + RAG context + tool-loop turns stay under num_ctx (prompts
    # larger than num_ctx get truncated by Ollama). Reduce if KV cache
    # pressure is high; raise only if VRAM comfortably allows it.
    ollama_context_length: int = 8192
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

    # Updated file watcher: when True, the CLI ``jarvis-ingest`` command and
    # the /documents/ingest-folder endpoint both shortcut to "full re-ingest"
    # semantics. A future incremental watcher can key off this flag.
    # When True, a lightweight file watcher can run in the background and
    # re-chunk changed files into Chroma. Off by default to avoid I/O on
    # machines where the RAG store is static.
    auto_reindex_enabled: bool = False
    # Polling interval (seconds) for the background file watcher when enabled.
    auto_reindex_interval: int = 300

    @property
    def complex_models(self) -> list[str]:
        return [m.strip() for m in self.complex_model_chain.split(",") if m.strip()]


# ---------------------------------------------------------------------------
# Compat gate: only emit Ollama options the installed version understands.
# ---------------------------------------------------------------------------

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
    if s.ollama_num_gpu == 0:
        warnings.append(
            "OLLAMA_NUM_GPU=0 disables GPU offload (pure CPU). Set OLLAMA_NUM_GPU=-1 "
            "to force full GPU execution and avoid spilling into system RAM."
        )
    if s.ollama_num_gpu not in (-1, 0) and s.ollama_num_gpu < 1:
        warnings.append(
            "OLLAMA_NUM_GPU must be -1 (offload all layers) or a positive layer count."
        )
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
    if not (0.0 <= s.rag_relevance_threshold <= 2.0):
        warnings.append(
            "RAG_RELEVANCE_THRESHOLD should be in (0, 1]; ~0.5 for qwen3-embedding. "
            "0 disables the relevance gate."
        )
    if s.history_max_turns < 1:
        warnings.append("HISTORY_MAX_TURNS < 1 disables history entirely.")
    if s.runtime_mode not in ("local", "docker", "auto"):
        warnings.append(
            f"RUNTIME_MODE='{s.runtime_mode}' is invalid; use 'local', 'docker' or 'auto'."
        )
    if s.runtime_mode == "local" and s.postgres_dsn:
        warnings.append(
            "RUNTIME_MODE=local does not require Docker. POSTGRES_DSN is set, so the "
            "app will still use Postgres — for a pure local deployment clear "
            "POSTGRES_DSN or set RUNTIME_MODE=docker."
        )
    if s.runtime_mode == "docker" and not s.postgres_dsn:
        warnings.append(
            "RUNTIME_MODE=docker expects POSTGRES_DSN; with it empty the app falls "
            "back to SQLite (still works without Docker)."
        )
    return warnings


settings = Settings()
