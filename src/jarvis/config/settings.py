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
    # Phase 7 :: performance / cost guardrails for the cloud (OpenRouter) path.
    # Estimated prompt-token ceiling before a cloud call is allowed. 0 =
    # unlimited (legacy behaviour). A prompt over this cap falls back to the
    # local general branch instead of spending on a huge cloud request.
    cloud_max_prompt_tokens: int = 0
    # Daily cloud-spend budget in USD. 0 = unlimited (legacy). When the
    # accumulated estimated spend crosses this budget, cloud calls are
    # refused for the rest of the day and the complex branch falls back to
    # local models. Estimates are rough (prompt-only, $/1M tokens per model
    # table) — an explicit guard against runaway spend, not an invoice.
    cloud_daily_budget_usd: float = 0.0

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
    # Maximum number of steps a background task's plan may contain. A
    # planning node decomposes a long-running prompt into at most this many
    # steps before execution. 0 = no planning node (legacy: the whole prompt
    # runs as one graph invocation).
    max_plan_steps: int = 8
    # Hard wall-clock cap for a single background task (seconds). 0 =
    # unlimited (legacy behaviour). When exceeded, the task is cancelled
    # with a clear error instead of running forever.
    max_task_duration_seconds: int = 0
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

    # --- Phase 8 :: todos & reminders ---
    # How often the reminder worker scans for due-soon todos (seconds).
    # 0 disables the worker thread (a single scan still runs at startup).
    todo_reminder_scan_interval_seconds: int = 300
    # How far ahead (minutes) a todo's due_at must be to trigger a reminder.
    todo_reminder_lookahead_minutes: int = 30

    # --- Phase 8 :: calendar integration ---
    # Master switch. When false (default) calendar routes/tools report a
    # structured "not configured" response and never touch the network.
    calendar_enabled: bool = False
    # Registry name of the provider (e.g. "google_calendar"). Empty = off.
    calendar_provider: str = ""
    # Path to a JSON credentials file the provider reads itself. Never
    # stored in the DB and never logged.
    calendar_credentials_path: str = ""
    # Default calendar to create events in when the caller doesn't specify one.
    calendar_default_calendar_id: str = ""

    # --- Phase 8 :: email drafts ---
    # Master switch for *sending*. Drafts work locally regardless. When false
    # the send endpoint/tool report "not configured" and never touch network.
    email_enabled: bool = False
    # Registry name of the provider (e.g. "smtp"). Empty = off.
    email_provider: str = ""
    # Path to a JSON credentials file the provider reads itself. Never
    # stored in the DB and never logged.
    email_credentials_path: str = ""
    # From-address used when the draft doesn't specify one.
    email_default_from: str = ""

    # --- Phase 8 :: external connectors ---
    # Master switch. When false (default) connector routes/tools report a
    # structured "not configured" response and never touch external services.
    connectors_enabled: bool = False
    # JSON file listing configured connectors (see docs/integrations.md).
    connectors_config_path: str = "./config/connectors.json"

    # --- Phase 8 :: IDE integration ---
    # Master switch. When false (default) /ide routes report "not configured".
    ide_integration_enabled: bool = False
    # Absolute path to the workspace all IDE operations are confined to.
    # Empty = not configured (nothing runs).
    ide_workspace_root: str = ""

    # --- Phase 8 :: voice interface ---
    # Master switches. When false (default) /voice routes report "not
    # configured" and never touch a speech API.
    voice_input_enabled: bool = False
    voice_output_enabled: bool = False
    # Registry names of the providers (e.g. "whisper_local", "edge_tts").
    # Empty = off.
    voice_input_provider: str = ""
    voice_output_provider: str = ""
    # Path to a JSON credentials file the providers read themselves. Never
    # stored in the DB and never logged.
    voice_credentials_path: str = ""

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
    # Master switch for the whole RAG pipeline. When False, build_context
    # never queries the vector store and no retrieved context is injected.
    # The document store itself is left untouched (uploads/ingest still work).
    rag_enabled: bool = True
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

# --- Phase 5 :: RAG retrieval quality ---
    # Preferred Phase 5 names for the RAG quality knobs. The legacy fields
    # above (rag_relevance_threshold, rerank_keyword_weight) remain
    # honoured for backward compatibility — see the resolvers below.
    #
    # Minimum relevance score (cosine similarity, 0..1) for retrieved chunks.
    # Chroma reports *distance*; this setting is expressed as similarity so
    # RAG_MIN_RELEVANCE_SCORE=0.5 means distance <= 0.5. Mirrors the legacy
    # rag_relevance_threshold. 0 disables the gate.
    rag_min_relevance_score: float = 0.5
    # Independent weights for the hybrid retrieval combination. When only one
    # is set, the other is implied as (1 - set). When both are None the
    # legacy single-knob `rerank_keyword_weight` is used, so existing configs
    # keep their exact behaviour.
    rag_vector_weight: float | None = None
    rag_keyword_weight: float | None = None
    # When False, retrieval is pure vector similarity (the BM25 layer is
    # skipped entirely), matching "vector-only" mode.
    rag_rerank_enabled: bool = True
    # Cap on the number of chunks returned per distinct source after rerank.
    # 0 = unlimited. Prevents a single large document from dominating the
    # retrieved context at the expense of other relevant sources.
    retrieval_per_source_limit: int = 0

    # --- Phase 10 :: Advanced RAG pipeline ---
    # Master switch for hybrid retrieval (dense + sparse).
    rag_hybrid_retrieval_enabled: bool = True
    # Weight for dense (embedding) retrieval in hybrid fusion.
    rag_dense_weight: float = 0.7
    # Weight for sparse (keyword/BM25) retrieval in hybrid fusion.
    rag_sparse_weight: float = 0.3
    # Enable query expansion (synonyms, related concepts, abbreviations).
    rag_query_expansion_enabled: bool = True
    # Maximum number of query variants to generate (including original).
    rag_query_expansion_max_variants: int = 3
    # Enable cross-encoder re-ranking of initial retrieval results.
    rag_reranking_enabled: bool = True
    # Cross-encoder model name (empty = use simple scoring, no external model).
    rag_reranking_model: str = ""
    # Initial retrieval k for re-ranking pipeline.
    rag_initial_retrieval_k: int = 50
    # Final retrieval n after re-ranking.
    rag_final_retrieval_n: int = 5

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

    # --- Phase 6 :: GPU benchmark / load-test framework ---
    # Hard wall-clock cap for a single benchmark generation (seconds). A run
    # that exceeds it is recorded as a timeout failure instead of hanging.
    benchmark_max_latency_seconds: int = 60
    # Thresholds recorded (never fail the run) unless set to 0 (disabled).
    benchmark_min_gpu_utilization_percent: float = 1.0
    benchmark_max_cpu_ram_mb: int = 16000
    benchmark_max_vram_percent: float = 95.0
    # Comma-separated context sizes the benchmark suite exercises by default.
    benchmark_context_sizes: str = "4096,6144,8192"
    # Directory for benchmark JSON / markdown reports and baselines.
    benchmark_output_dir: str = "./reports"

    # --- Phase 6 :: safe GPU execution policy ---
    # Allowed: "prefer_gpu" | "require_gpu" | "allow_cpu".
    #   prefer_gpu — prefer full GPU; allow controlled partial offload or
    #                explicit CPU fallback only when the flags below permit,
    #                always surfaced in metadata/logs.
    #   require_gpu — refuse to run on CPU; a model that cannot load fully on
    #                 the GPU returns a structured GPU-capacity error.
    #   allow_cpu — CPU fallback allowed but always visible in metadata/logs.
    gpu_policy: str = "prefer_gpu"
    # Whether a model that cannot fit fully in VRAM may retry with a partial
    # or CPU offload (subject to the policy above).
    gpu_allow_cpu_fallback: bool = True
    # When True, "require_gpu" additionally demands 100% GPU offload (no
    # partial CPU/GPU at all). When False, require_gpu accepts any offload
    # that includes the GPU.
    gpu_require_full_offload: bool = False
    # VRAM usage cap (percent of total) above which a model is considered to
    # exceed available GPU memory. 0 disables the VRAM pre-check.
    gpu_max_vram_percent: float = 95.0
    # Minimum free VRAM (MB) required before a model is loaded. 0 disables.
    gpu_min_free_vram_mb: int = 512
    # Whether the strong local model is allowed to run partially offloaded
    # (CPU/GPU) when it exceeds VRAM. False = it must fit fully or be routed
    # to a fallback / background task / structured warning.
    gpu_strong_model_allow_partial_offload: bool = False
    # Whether the GPU policy runs the runtime/VRAM pre-checks at all.
    gpu_runtime_check_enabled: bool = True

    # --- Phase 6 :: session-token security ---
    # Lifetime of a session token before it expires (hours). 0 = never expire.
    session_token_ttl_hours: int = 168
    # After how long a still-valid token should be rotated (hours). 0 = no
    # automatic rotation prompt (rotation still available via API).
    session_token_rotation_hours: int = 72
    # Hash scheme for session tokens at rest: "argon2" | "bcrypt" | "pbkdf2".
    # argon2 is preferred; bcrypt and pbkdf2 are accepted fallbacks.
    session_token_hash_scheme: str = "argon2"

    # --- Phase 6 :: cloud cost tracking + budgets ---
    # Master switch for persistent cloud-usage records + budgets.
    cloud_cost_tracking_enabled: bool = True
    # When True, cloud calls need explicit approval before spending.
    cloud_require_cost_approval: bool = True
    # Max estimated cost (USD) for a single cloud call. 0 = unlimited.
    cloud_max_request_cost_usd: float = 0.25
    # Max estimated cost (USD) per session. 0 = unlimited.
    cloud_max_session_cost_usd: float = 2.00
    # Path to the model pricing config (JSON). See config/model_pricing.json.
    cloud_pricing_config_path: str = "./config/model_pricing.json"

    # --- Phase 6 :: observability / trace retention ---
    # Max in-memory traces retained for GET /traces/recent.
    trace_retention_limit: int = 256

    # --- Phase 11 :: User and Role Management ---
    # Master switch for user management features.
    user_management_enabled: bool = False
    # Default role assigned to new users.
    default_role: str = "user"
    # Minimum password length.
    password_min_length: int = 12
    # Require special characters in passwords.
    password_require_special_char: bool = True
    # Maximum number of concurrent sessions per user.
    session_max_per_user: int = 10

    # --- Phase 12 :: Two-Factor Authentication ---
    # Master switch for 2FA features.
    two_factor_auth_enabled: bool = False
    # Require 2FA for admin users.
    two_factor_required_for_admins: bool = True
    # How long to remember a device after successful 2FA (days).
    two_factor_remember_device_days: int = 30
    # Number of recovery codes to generate during enrollment.
    two_factor_recovery_codes_count: int = 10

    # --- Phase 13 :: Deep Thinking Mode ---
    # Master switch for deep thinking mode.
    deep_thinking_enabled: bool = True
    # Automatically trigger deep thinking for complex questions.
    deep_thinking_auto_trigger: bool = True
    # Confidence threshold (0..1) for auto-triggering deep thinking.
    deep_thinking_auto_trigger_confidence_threshold: float = 0.7
    # Maximum number of reasoning steps in deep thinking.
    deep_thinking_max_reasoning_steps: int = 5
    # Token multiplier for deep thinking (multiplies context budget).
    deep_thinking_max_tokens_factor: float = 3.0
    # Show reasoning chain in response (can be overridden per-request).
    deep_thinking_show_reasoning_chain: bool = False

    # --- Phase 13 :: Reasoning Strategy Variations ---
    # Default reasoning strategy: auto|cot|tot|self_consistency|reflexion|fast_and_slow
    reasoning_strategy_default: str = "auto"
    # Enable Chain-of-Thought reasoning.
    reasoning_strategy_cot_enabled: bool = True
    # Enable Tree-of-Thought reasoning.
    reasoning_strategy_tot_enabled: bool = True
    # Maximum branches for ToT.
    reasoning_strategy_tot_max_branches: int = 3
    # Enable Self-Consistency reasoning.
    reasoning_strategy_self_consistency_enabled: bool = True
    # Number of samples for self-consistency.
    reasoning_strategy_self_consistency_num_samples: int = 3
    # Enable Reflexion reasoning.
    reasoning_strategy_reflexion_enabled: bool = True
    # Maximum iterations for Reflexion.
    reasoning_strategy_reflexion_max_iterations: int = 2
    # Enable Fast-and-Slow reasoning.
    reasoning_strategy_fast_and_slow_enabled: bool = True

    # --- Phase 13 :: A/B testing for reasoning strategies ---
    # Master switch for A/B testing of reasoning strategies. When False,
    # traffic splitting always routes to the control (variant A) and metric
    # analysis reports are suppressed from the API/CLI.
    ab_testing_reasoning_enabled: bool = True
    # Minimum number of samples required per variant before a test is
    # considered to have enough data to declare a winner.
    ab_testing_min_samples_per_variant: int = 50
    # Statistical significance threshold (alpha) for the z-test / chi-square
    # analysis. A p-value at or below this is treated as significant.
    ab_testing_significance_threshold: float = 0.05

    # --- Phase 7 :: deployment profile ---
    # One of: local | single_host | production. Drives safe defaults and
    # validation (see jarvis.config.deployment). local = localhost-only dev;
    # single_host = one private machine; production = hardened public-facing.
    deployment_profile: str = "local"
    # Host/port the FastAPI backend binds. Local defaults to loopback only.
    jarvis_host: str = "127.0.0.1"
    jarvis_port: int = 8000
    # Comma-separated allowed browser origins for CORS. Empty = same-origin
    # only (Streamlit talks to the backend server-side). "*" is rejected by
    # production/single-host validation.
    jarvis_allowed_origins: str = ""
    # Comma-separated host header allowlist enforced by TrustedHostMiddleware.
    jarvis_trusted_hosts: str = "localhost,127.0.0.1"
    # When True, proxy headers (X-Forwarded-For/Host/Proto) are honoured.
    # Only enable behind a trusted reverse proxy.
    jarvis_behind_reverse_proxy: bool = False
    # When True, HSTS is advertised and the app warns if a request arrives
    # over plain HTTP (only meaningful in single_host/production).
    jarvis_force_https: bool = False
    # Debug mode (fastapi --reload style diagnostics). Forced to warn in
    # production; keep off in production.
    jarvis_debug: bool = False
    # Whether GET /traces/recent (detailed per-request trace exposure) is
    # enabled. Production validation flags it unless explicitly disabled.
    jarvis_expose_traces: bool = True
    # Backup tooling switch. Production validation requires it to be on.
    jarvis_backup_enabled: bool = False
    # Directory backups are written to (never auto-deleted).
    backup_dir: str = "./backups"
    # Include document source files in backups only when explicitly enabled
    # (CLI flag --include-documents or this setting). Document metadata is
    # always included in the vector store backup.
    backup_include_documents: bool = False
    # Retention guidance (days) shown by jarvis-admin/backup reports. Advisory
    # only — the tooling never deletes old backups automatically.
    backup_retention_days: int = 30

    @property
    def complex_models(self) -> list[str]:
        return [m.strip() for m in self.complex_model_chain.split(",") if m.strip()]

    @property
    def allowed_origins_list(self) -> list[str]:
        """Parsed JARVIS_ALLOWED_ORIGINS (empty = same-origin only)."""
        return [
            o.strip() for o in self.jarvis_allowed_origins.split(",") if o.strip()
        ]

    @property
    def trusted_hosts_list(self) -> list[str]:
        """Parsed JARVIS_TRUSTED_HOSTS."""
        return [
            h.strip() for h in self.jarvis_trusted_hosts.split(",") if h.strip()
        ]

    @property
    def benchmark_context_sizes_list(self) -> list[int]:
        """Context sizes the benchmark suite should exercise (BENCHMARK_CONTEXT_SIZES)."""
        out: list[int] = []
        for part in self.benchmark_context_sizes.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                value = int(part)
            except ValueError:
                continue
            if value > 0 and value not in out:
                out.append(value)
        return out or [4096]

    # ------------------------------------------------------------------
    # Phase 5 :: backward-compatible resolvers for the RAG quality knobs.
    # The legacy fields (rag_relevance_threshold, rerank_keyword_weight)
    # keep working unchanged; the new Phase 5 fields take precedence when
    # they are actually set.
    # ------------------------------------------------------------------

    @property
    def effective_relevance_threshold(self) -> float:
        """Relevance gate in *distance* units (Chroma cosine distance).

        ``rag_min_relevance_score`` is expressed as similarity (0..1).
        The legacy ``rag_relevance_threshold`` was already distance units.
        When the new field holds its default we defer to the legacy field
        so existing configs are untouched; 0 disables the gate.
        """
        if self.rag_min_relevance_score != 0.5 or self.rag_relevance_threshold == 0.5:
            return self.rag_min_relevance_score
        return self.rag_relevance_threshold

    @property
    def effective_vector_weight(self) -> float:
        """Vector weight for hybrid retrieval (0..1).

        Falls back to ``1 - rerank_keyword_weight`` for the legacy
        single-knob config. When the new two-weight settings are both set
        they are normalised so they sum to 1.
        """
        v = self.rag_vector_weight
        k = self.rag_keyword_weight
        if v is not None and k is not None:
            total = (v + k) or 1.0
            return max(0.0, min(1.0, v / total))
        if v is not None:
            return max(0.0, min(1.0, v))
        legacy_kw = max(0.0, min(1.0, self.rerank_keyword_weight))
        return 1.0 - legacy_kw

    @property
    def effective_keyword_weight(self) -> float:
        v = self.rag_vector_weight
        k = self.rag_keyword_weight
        if v is not None and k is not None:
            total = (v + k) or 1.0
            return max(0.0, min(1.0, k / total))
        if k is not None:
            return max(0.0, min(1.0, k))
        legacy_kw = max(0.0, min(1.0, self.rerank_keyword_weight))
        return legacy_kw

    # ------------------------------------------------------------------
    # Phase 10 :: backward-compatible resolvers for advanced RAG pipeline.
    # ------------------------------------------------------------------

    @property
    def effective_dense_weight(self) -> float:
        """Dense (embedding) weight for hybrid retrieval (0..1).

        Falls back to legacy ``effective_vector_weight`` when Phase 10
        specific weights are not set.
        """
        if self.rag_dense_weight != 0.7 or self.rag_sparse_weight != 0.3:
            total = (self.rag_dense_weight + self.rag_sparse_weight) or 1.0
            return max(0.0, min(1.0, self.rag_dense_weight / total))
        return self.effective_vector_weight

    @property
    def effective_sparse_weight(self) -> float:
        """Sparse (keyword/BM25) weight for hybrid retrieval (0..1)."""
        if self.rag_dense_weight != 0.7 or self.rag_sparse_weight != 0.3:
            total = (self.rag_dense_weight + self.rag_sparse_weight) or 1.0
            return max(0.0, min(1.0, self.rag_sparse_weight / total))
        return self.effective_keyword_weight


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
    if not (0.0 <= s.rag_min_relevance_score <= 2.0):
        warnings.append(
            "RAG_MIN_RELEVANCE_SCORE should be in (0, 1]; ~0.5 for qwen3-embedding. "
            "0 disables the relevance gate."
        )
    if s.rag_vector_weight is not None and not (0.0 <= s.rag_vector_weight <= 1.0):
        warnings.append("RAG_VECTOR_WEIGHT must be in [0, 1].")
    if s.rag_keyword_weight is not None and not (0.0 <= s.rag_keyword_weight <= 1.0):
        warnings.append("RAG_KEYWORD_WEIGHT must be in [0, 1].")
    if s.rag_vector_weight is not None and s.rag_keyword_weight is not None:
        total = s.rag_vector_weight + s.rag_keyword_weight
        if total <= 0:
            warnings.append(
                "RAG_VECTOR_WEIGHT + RAG_KEYWORD_WEIGHT must be > 0 (both are zero)."
            )
    if s.retrieval_per_source_limit < 0:
        warnings.append("RETRIEVAL_PER_SOURCE_LIMIT must be >= 0 (0 = unlimited).")
    if s.rag_hybrid_retrieval_enabled:
        if not (0.0 <= s.rag_dense_weight <= 1.0):
            warnings.append("RAG_DENSE_WEIGHT must be in [0, 1].")
        if not (0.0 <= s.rag_sparse_weight <= 1.0):
            warnings.append("RAG_SPARSE_WEIGHT must be in [0, 1].")
        total = s.rag_dense_weight + s.rag_sparse_weight
        if total <= 0:
            warnings.append("RAG_DENSE_WEIGHT + RAG_SPARSE_WEIGHT must be > 0 (both are zero).")
    if s.rag_query_expansion_enabled and s.rag_query_expansion_max_variants < 1:
        warnings.append("RAG_QUERY_EXPANSION_MAX_VARIANTS must be >= 1.")
    if s.rag_reranking_enabled and s.rag_initial_retrieval_k < 1:
        warnings.append("RAG_INITIAL_RETRIEVAL_K must be >= 1.")
    if s.rag_reranking_enabled and s.rag_final_retrieval_n < 1:
        warnings.append("RAG_FINAL_RETRIEVAL_N must be >= 1.")
    if s.rag_reranking_enabled and s.rag_final_retrieval_n > s.rag_initial_retrieval_k:
        warnings.append("RAG_FINAL_RETRIEVAL_N must be <= RAG_INITIAL_RETRIEVAL_K.")
    if s.max_plan_steps < 0:
        warnings.append("MAX_PLAN_STEPS must be >= 0 (0 = no planning node).")
    if s.max_task_duration_seconds < 0:
        warnings.append("MAX_TASK_DURATION_SECONDS must be >= 0 (0 = unlimited).")
    if s.cloud_max_prompt_tokens < 0:
        warnings.append("CLOUD_MAX_PROMPT_TOKENS must be >= 0 (0 = unlimited).")
    if s.cloud_daily_budget_usd < 0:
        warnings.append("CLOUD_DAILY_BUDGET_USD must be >= 0 (0 = unlimited).")
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
    if s.gpu_policy not in ("prefer_gpu", "require_gpu", "allow_cpu"):
        warnings.append(
            f"GPU_POLICY='{s.gpu_policy}' is invalid; use 'prefer_gpu', 'require_gpu' or 'allow_cpu'."
        )
    if s.gpu_policy == "require_gpu" and s.gpu_require_full_offload and s.gpu_allow_cpu_fallback:
        warnings.append(
            "GPU_POLICY=require_gpu with GPU_REQUIRE_FULL_OFFLOAD=true conflicts with "
            "GPU_ALLOW_CPU_FALLBACK=true (full-offload requirement makes CPU fallback "
            "inaccessible)."
        )
    if not (0 <= s.gpu_max_vram_percent <= 100):
        warnings.append("GPU_MAX_VRAM_PERCENT must be in [0, 100] (0 disables the VRAM check).")
    if s.gpu_min_free_vram_mb < 0:
        warnings.append("GPU_MIN_FREE_VRAM_MB must be >= 0.")
    if s.benchmark_max_latency_seconds < 1:
        warnings.append("BENCHMARK_MAX_LATENCY_SECONDS must be >= 1.")
    if s.session_token_hash_scheme not in ("argon2", "bcrypt", "pbkdf2"):
        warnings.append(
            f"SESSION_TOKEN_HASH_SCHEME='{s.session_token_hash_scheme}' is invalid; "
            "use 'argon2', 'bcrypt' or 'pbkdf2'."
        )
    if s.session_token_ttl_hours < 0:
        warnings.append("SESSION_TOKEN_TTL_HOURS must be >= 0 (0 = never expire).")
    if s.cloud_max_request_cost_usd < 0 or s.cloud_max_session_cost_usd < 0:
        warnings.append("CLOUD_MAX_REQUEST_COST_USD / CLOUD_MAX_SESSION_COST_USD must be >= 0.")
    if s.trace_retention_limit < 1:
        warnings.append("TRACE_RETENTION_LIMIT must be >= 1.")
    if s.jarvis_port < 1 or s.jarvis_port > 65535:
        warnings.append("JARVIS_PORT must be in [1, 65535].")
    if not s.jarvis_host:
        warnings.append("JARVIS_HOST is empty.")
    if s.backup_retention_days < 0:
        warnings.append("BACKUP_RETENTION_DAYS must be >= 0.")
    if s.user_management_enabled:
        if s.password_min_length < 8:
            warnings.append("PASSWORD_MIN_LENGTH must be >= 8.")
        if s.session_max_per_user < 1:
            warnings.append("SESSION_MAX_PER_USER must be >= 1.")
    if s.two_factor_auth_enabled:
        if s.two_factor_remember_device_days < 1:
            warnings.append("TWO_FACTOR_REMEMBER_DEVICE_DAYS must be >= 1.")
        if s.two_factor_recovery_codes_count < 1:
            warnings.append("TWO_FACTOR_RECOVERY_CODES_COUNT must be >= 1.")
    if s.deep_thinking_enabled:
        if not (0.0 <= s.deep_thinking_auto_trigger_confidence_threshold <= 1.0):
            warnings.append("DEEP_THINKING_AUTO_TRIGGER_CONFIDENCE_THRESHOLD must be in [0, 1].")
        if s.deep_thinking_max_reasoning_steps < 1:
            warnings.append("DEEP_THINKING_MAX_REASONING_STEPS must be >= 1.")
        if s.deep_thinking_max_tokens_factor < 1.0:
            warnings.append("DEEP_THINKING_MAX_TOKENS_FACTOR must be >= 1.0.")
    # --- Phase 13 :: Reasoning Strategy validation ---
    if s.reasoning_strategy_default not in ("auto", "cot", "tot", "self_consistency", "reflexion", "fast_and_slow"):
        warnings.append(
            f"REASONING_STRATEGY_DEFAULT='{s.reasoning_strategy_default}' is invalid; "
            "use 'auto', 'cot', 'tot', 'self_consistency', 'reflexion', or 'fast_and_slow'."
        )
    if s.reasoning_strategy_tot_max_branches < 1:
        warnings.append("REASONING_STRATEGY_TOT_MAX_BRANCHES must be >= 1.")
    if s.reasoning_strategy_self_consistency_num_samples < 1:
        warnings.append("REASONING_STRATEGY_SELF_CONSISTENCY_NUM_SAMPLES must be >= 1.")
    if s.reasoning_strategy_reflexion_max_iterations < 1:
        warnings.append("REASONING_STRATEGY_REFLEXION_MAX_ITERATIONS must be >= 1.")
    return warnings


settings = Settings()
