"""Ollama connection management with health checks and caching.

Provides a singleton connection manager that validates Ollama availability,
caches connection state, and provides helpful error messages when Ollama
is unreachable.
"""
import logging
import time
from threading import Lock
from typing import Optional

import httpx

from jarvis.config.settings import settings

logger = logging.getLogger(__name__)


class OllamaConnectionManager:
    """Thread-safe singleton for managing Ollama connections."""

    _instance: Optional['OllamaConnectionManager'] = None
    _lock = Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self._last_check_time = 0.0
        self._last_check_result = False
        self._cache_ttl = 30.0  # Cache health check for 30 seconds
        self._client: Optional[httpx.Client] = None
        self._client_lock = Lock()

    def _create_client(self) -> httpx.Client:
        """Create a new HTTP client with appropriate timeouts."""
        return httpx.Client(
            base_url=settings.ollama_base_url,
            timeout=httpx.Timeout(
                connect=5.0,
                read=300.0,  # Long timeout for generation
                write=5.0,
                pool=5.0,
            ),
            limits=httpx.Limits(
                max_connections=10,
                max_keepalive_connections=5,
            ),
        )

    def get_client(self) -> httpx.Client:
        """Get or create the shared HTTP client."""
        with self._client_lock:
            if self._client is None:
                self._client = self._create_client()
            return self._client

    def close(self):
        """Close the HTTP client connection pool."""
        with self._client_lock:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception as exc:
                    logger.debug("Error closing Ollama client: %s", exc)
                finally:
                    self._client = None

    def check_health(self, force: bool = False) -> tuple[bool, Optional[str]]:
        """Check if Ollama is reachable and healthy.

        Args:
            force: Skip cache and force a fresh check

        Returns:
            (is_healthy, error_message)
        """
        now = time.monotonic()

        # Return cached result if still valid
        if not force and (now - self._last_check_time) < self._cache_ttl:
            return self._last_check_result, None

        try:
            client = self.get_client()
            response = client.get("/api/tags", timeout=5.0)
            response.raise_for_status()

            self._last_check_time = now
            self._last_check_result = True
            return True, None

        except httpx.ConnectError as exc:
            error_msg = (
                f"Cannot connect to Ollama at {settings.ollama_base_url}. "
                f"Is Ollama running? Start it with 'ollama serve'."
            )
            logger.error(error_msg)
            self._last_check_time = now
            self._last_check_result = False
            return False, error_msg

        except httpx.TimeoutException:
            error_msg = f"Ollama at {settings.ollama_base_url} is not responding (timeout)."
            logger.error(error_msg)
            self._last_check_time = now
            self._last_check_result = False
            return False, error_msg

        except httpx.HTTPStatusError as exc:
            error_msg = (
                f"Ollama returned error {exc.response.status_code}: "
                f"{exc.response.text[:200]}"
            )
            logger.error(error_msg)
            self._last_check_time = now
            self._last_check_result = False
            return False, error_msg

        except Exception as exc:
            error_msg = f"Unexpected error checking Ollama health: {exc.__class__.__name__}: {exc}"
            logger.error(error_msg)
            self._last_check_time = now
            self._last_check_result = False
            return False, error_msg

    def verify_model_exists(self, model_name: str) -> tuple[bool, Optional[str]]:
        """Verify that a specific model is available in Ollama.

        Args:
            model_name: Name of the model to check

        Returns:
            (exists, error_message)
        """
        try:
            client = self.get_client()
            response = client.get("/api/tags", timeout=5.0)
            response.raise_for_status()

            data = response.json()
            models = data.get("models", [])
            available = {m.get("name", "").split(":")[0] for m in models}

            # Check both with and without tag
            model_base = model_name.split(":")[0]
            if model_name in {m.get("name") for m in models} or model_base in available:
                return True, None

            error_msg = (
                f"Model '{model_name}' not found in Ollama. "
                f"Available models: {', '.join(sorted(available)) or 'none'}. "
                f"Pull it with 'ollama pull {model_name}'."
            )
            return False, error_msg

        except Exception as exc:
            error_msg = f"Failed to verify model '{model_name}': {exc.__class__.__name__}: {exc}"
            return False, error_msg

    def invalidate_cache(self):
        """Force the next health check to be fresh."""
        self._last_check_time = 0.0


# Global singleton instance
_connection_manager = OllamaConnectionManager()


def get_connection_manager() -> OllamaConnectionManager:
    """Get the global Ollama connection manager."""
    return _connection_manager


def check_ollama_available() -> tuple[bool, Optional[str]]:
    """Quick health check for Ollama availability.

    Returns:
        (is_available, error_message)
    """
    return _connection_manager.check_health()


def ensure_ollama_ready(model_name: Optional[str] = None) -> None:
    """Ensure Ollama is ready and optionally verify a model exists.

    Raises:
        ConnectionError: If Ollama is not reachable
        ValueError: If the specified model is not available
    """
    is_healthy, error = _connection_manager.check_health()
    if not is_healthy:
        raise ConnectionError(error or "Ollama is not available")

    if model_name:
        exists, error = _connection_manager.verify_model_exists(model_name)
        if not exists:
            raise ValueError(error or f"Model '{model_name}' not available")


__all__ = [
    "OllamaConnectionManager",
    "get_connection_manager",
    "check_ollama_available",
    "ensure_ollama_ready",
]
