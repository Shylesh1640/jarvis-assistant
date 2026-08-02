"""Wrapper around local Ollama models via LangChain."""
from langchain_ollama import ChatOllama

from jarvis.config.settings import settings


def get_general_model(temperature: float = 0.4) -> ChatOllama:
    return ChatOllama(
        model=settings.general_model,
        base_url=settings.ollama_base_url,
        temperature=temperature,
    )


def get_strong_local_model(temperature: float = 0.3) -> ChatOllama:
    return ChatOllama(
        model=settings.strong_local_model,
        base_url=settings.ollama_base_url,
        temperature=temperature,
    )


def get_coding_model(temperature: float = 0.2) -> ChatOllama:
    return ChatOllama(
        model=settings.coding_model,
        base_url=settings.ollama_base_url,
        temperature=temperature,
    )


# Per-intent default temperatures for dynamic model selection.
_TEMPERATURE_BY_INTENT = {
    "general": 0.4,
    "coding": 0.2,
    "complex": 0.3,
}


def get_model_named(model_name: str, intent: str = "general", temperature: float | None = None) -> ChatOllama:
    """Build a ChatOllama for an explicitly-chosen model name.

    Used by the branches after `select_model(state, settings)` has decided
    which model to run. If `temperature` is None we pick a sane default
    based on the branch intent.
    """
    temp = temperature if temperature is not None else _TEMPERATURE_BY_INTENT.get(intent, 0.4)
    return ChatOllama(
        model=model_name,
        base_url=settings.ollama_base_url,
        temperature=temp,
    )
