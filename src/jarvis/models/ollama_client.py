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
