"""Dynamic model selection based on intent + complexity.

Phase 4 :: Smarter routing + model choice.

`select_model(state, settings)` is the single source of truth for which
model a branch should use for a given turn. Returning just the model name
(string) keeps branches free to build whatever client they need
(ChatOllama with `.bind_tools(...)`, OpenRouter HTTP, etc.).
"""
from __future__ import annotations

import logging

from jarvis.config.settings import Settings
from jarvis.orchestration.state import JarvisState

logger = logging.getLogger(__name__)

ModelName = str


def select_model(state: JarvisState, settings: Settings) -> ModelName:
    """Pick a model name for this turn.

    Returns the *model name* (string). The branch is responsible for
    instantiating the actual client/caller.

    Routing matrix:
      general  + easy             -> general_model (small)
      general  + medium           -> strong_local_model (if enabled)
      general  + difficult        -> strong_local_model (if enabled)
      coding   + easy             -> coding_model_small
      coding   + medium/difficult -> coding_model (strong local coder)
      complex  + *                -> first cloud model in chain (branch handles
                                     fallback to general if cloud fails)
    """
    intent = state.get("intent", "general")
    complexity = state.get("complexity", "easy")

    if intent == "general":
        if complexity == "easy":
            return _pick(settings.general_model, reason="general+easy -> small general")
        # medium / difficult general
        if settings.use_strong_local and settings.strong_local_model:
            return _pick(
                settings.strong_local_model,
                reason=f"general+{complexity} -> strong local",
            )
        return _pick(
            settings.general_model,
            reason=f"general+{complexity} -> small general (strong local disabled)",
        )

    if intent == "coding":
        if complexity == "easy":
            small = settings.coding_model_small or settings.coding_model
            return _pick(small, reason="coding+easy -> small coder")
        return _pick(
            settings.coding_model,
            reason=f"coding+{complexity} -> strong coder",
        )

    if intent == "complex":
        # The branch owns the cloud fallback chain; we just hand it the
        # primary model name. If no cloud is configured it will fall back
        # to the general branch automatically.
        chain = settings.complex_models
        if chain:
            return _pick(chain[0], reason="complex -> cloud chain[0]")
        return _pick(settings.general_model, reason="complex -> small general (no cloud configured)")

    # Defensive fallback (shouldn't happen given the Literals)
    return _pick(settings.general_model, reason="fallback -> small general")


def _pick(model: str, *, reason: str) -> str:
    logger.info("Model selected: %s (%s)", model, reason)
    return model
