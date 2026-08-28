"""Settings API routes for deep thinking and reasoning strategies."""
from __future__ import annotations

import logging

from fastapi import APIRouter

from jarvis.api.errors import APIError
from jarvis.config.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/deep-thinking")
def get_deep_thinking_settings() -> dict:
    """Get current deep thinking settings."""
    return {
        "enabled": settings.deep_thinking_enabled,
        "auto_trigger": settings.deep_thinking_auto_trigger,
        "auto_trigger_confidence_threshold": settings.deep_thinking_auto_trigger_confidence_threshold,
        "max_reasoning_steps": settings.deep_thinking_max_reasoning_steps,
        "max_tokens_factor": settings.deep_thinking_max_tokens_factor,
        "show_reasoning_chain": settings.deep_thinking_show_reasoning_chain,
    }


@router.patch("/deep-thinking")
def update_deep_thinking_settings(payload: dict) -> dict:
    """Update deep thinking settings (runtime only, not persisted to .env)."""
    # Note: These are runtime-only changes. To persist, update .env and restart.
    updated = {}
    
    if "enabled" in payload:
        if not isinstance(payload["enabled"], bool):
            raise APIError(422, "invalid_type", "enabled must be a boolean")
        settings.deep_thinking_enabled = payload["enabled"]
        updated["enabled"] = settings.deep_thinking_enabled
    
    if "auto_trigger" in payload:
        if not isinstance(payload["auto_trigger"], bool):
            raise APIError(422, "invalid_type", "auto_trigger must be a boolean")
        settings.deep_thinking_auto_trigger = payload["auto_trigger"]
        updated["auto_trigger"] = settings.deep_thinking_auto_trigger
    
    if "auto_trigger_confidence_threshold" in payload:
        val = payload["auto_trigger_confidence_threshold"]
        if not isinstance(val, (int, float)) or not (0.0 <= val <= 1.0):
            raise APIError(422, "invalid_value", "auto_trigger_confidence_threshold must be a float in [0, 1]")
        settings.deep_thinking_auto_trigger_confidence_threshold = float(val)
        updated["auto_trigger_confidence_threshold"] = settings.deep_thinking_auto_trigger_confidence_threshold
    
    if "max_reasoning_steps" in payload:
        val = payload["max_reasoning_steps"]
        if not isinstance(val, int) or val < 1:
            raise APIError(422, "invalid_value", "max_reasoning_steps must be an integer >= 1")
        settings.deep_thinking_max_reasoning_steps = val
        updated["max_reasoning_steps"] = settings.deep_thinking_max_reasoning_steps
    
    if "max_tokens_factor" in payload:
        val = payload["max_tokens_factor"]
        if not isinstance(val, (int, float)) or val < 1.0:
            raise APIError(422, "invalid_value", "max_tokens_factor must be a number >= 1.0")
        settings.deep_thinking_max_tokens_factor = float(val)
        updated["max_tokens_factor"] = settings.deep_thinking_max_tokens_factor
    
    if "show_reasoning_chain" in payload:
        if not isinstance(payload["show_reasoning_chain"], bool):
            raise APIError(422, "invalid_type", "show_reasoning_chain must be a boolean")
        settings.deep_thinking_show_reasoning_chain = payload["show_reasoning_chain"]
        updated["show_reasoning_chain"] = settings.deep_thinking_show_reasoning_chain
    
    return {"updated": updated}


@router.get("/reasoning-strategies")
def get_reasoning_strategy_settings() -> dict:
    """Get current reasoning strategy settings."""
    return {
        "default": settings.reasoning_strategy_default,
        "cot_enabled": settings.reasoning_strategy_cot_enabled,
        "tot_enabled": settings.reasoning_strategy_tot_enabled,
        "tot_max_branches": settings.reasoning_strategy_tot_max_branches,
        "self_consistency_enabled": settings.reasoning_strategy_self_consistency_enabled,
        "self_consistency_num_samples": settings.reasoning_strategy_self_consistency_num_samples,
        "reflexion_enabled": settings.reasoning_strategy_reflexion_enabled,
        "reflexion_max_iterations": settings.reasoning_strategy_reflexion_max_iterations,
        "fast_and_slow_enabled": settings.reasoning_strategy_fast_and_slow_enabled,
    }


@router.patch("/reasoning-strategies")
def update_reasoning_strategy_settings(payload: dict) -> dict:
    """Update reasoning strategy settings (runtime only, not persisted to .env)."""
    updated = {}
    
    if "default" in payload:
        val = payload["default"]
        if val not in ("auto", "cot", "tot", "self_consistency", "reflexion", "fast_and_slow"):
            raise APIError(422, "invalid_value", "default must be one of: auto, cot, tot, self_consistency, reflexion, fast_and_slow")
        settings.reasoning_strategy_default = val
        updated["default"] = settings.reasoning_strategy_default
    
    if "cot_enabled" in payload:
        if not isinstance(payload["cot_enabled"], bool):
            raise APIError(422, "invalid_type", "cot_enabled must be a boolean")
        settings.reasoning_strategy_cot_enabled = payload["cot_enabled"]
        updated["cot_enabled"] = settings.reasoning_strategy_cot_enabled
    
    if "tot_enabled" in payload:
        if not isinstance(payload["tot_enabled"], bool):
            raise APIError(422, "invalid_type", "tot_enabled must be a boolean")
        settings.reasoning_strategy_tot_enabled = payload["tot_enabled"]
        updated["tot_enabled"] = settings.reasoning_strategy_tot_enabled
    
    if "tot_max_branches" in payload:
        val = payload["tot_max_branches"]
        if not isinstance(val, int) or val < 1:
            raise APIError(422, "invalid_value", "tot_max_branches must be an integer >= 1")
        settings.reasoning_strategy_tot_max_branches = val
        updated["tot_max_branches"] = settings.reasoning_strategy_tot_max_branches
    
    if "self_consistency_enabled" in payload:
        if not isinstance(payload["self_consistency_enabled"], bool):
            raise APIError(422, "invalid_type", "self_consistency_enabled must be a boolean")
        settings.reasoning_strategy_self_consistency_enabled = payload["self_consistency_enabled"]
        updated["self_consistency_enabled"] = settings.reasoning_strategy_self_consistency_enabled
    
    if "self_consistency_num_samples" in payload:
        val = payload["self_consistency_num_samples"]
        if not isinstance(val, int) or val < 1:
            raise APIError(422, "invalid_value", "self_consistency_num_samples must be an integer >= 1")
        settings.reasoning_strategy_self_consistency_num_samples = val
        updated["self_consistency_num_samples"] = settings.reasoning_strategy_self_consistency_num_samples
    
    if "reflexion_enabled" in payload:
        if not isinstance(payload["reflexion_enabled"], bool):
            raise APIError(422, "invalid_type", "reflexion_enabled must be a boolean")
        settings.reasoning_strategy_reflexion_enabled = payload["reflexion_enabled"]
        updated["reflexion_enabled"] = settings.reasoning_strategy_reflexion_enabled
    
    if "reflexion_max_iterations" in payload:
        val = payload["reflexion_max_iterations"]
        if not isinstance(val, int) or val < 1:
            raise APIError(422, "invalid_value", "reflexion_max_iterations must be an integer >= 1")
        settings.reasoning_strategy_reflexion_max_iterations = val
        updated["reflexion_max_iterations"] = settings.reasoning_strategy_reflexion_max_iterations
    
    if "fast_and_slow_enabled" in payload:
        if not isinstance(payload["fast_and_slow_enabled"], bool):
            raise APIError(422, "invalid_type", "fast_and_slow_enabled must be a boolean")
        settings.reasoning_strategy_fast_and_slow_enabled = payload["fast_and_slow_enabled"]
        updated["fast_and_slow_enabled"] = settings.reasoning_strategy_fast_and_slow_enabled
    
    return {"updated": updated}