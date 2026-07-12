"""Central app settings loaded from environment variables."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ollama_base_url: str = "http://localhost:11434"
    general_model: str = "qwen3:8b"
    strong_local_model: str = "qwen3:14b"
    coding_model: str = "qwen3-coder:30b"
    embedding_model: str = "qwen3-embedding:latest"

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    complex_model_chain: str = "anthropic/claude-opus-4.1,openai/gpt-5.5,google/gemini-2.5-pro"

    app_env: str = "development"
    log_level: str = "INFO"
    vector_db_path: str = "./data/vector_store"

    @property
    def complex_models(self) -> list[str]:
        return [m.strip() for m in self.complex_model_chain.split(",") if m.strip()]


settings = Settings()
