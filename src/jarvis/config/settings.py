"""Central app settings loaded from environment variables."""
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    @property
    def complex_models(self) -> list[str]:
        return [m.strip() for m in self.complex_model_chain.split(",") if m.strip()]


settings = Settings()
