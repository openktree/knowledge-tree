"""Configuration endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from kt_config.settings import get_settings

router = APIRouter(prefix="/api/v1/config", tags=["config"])


@router.get("/models")
async def get_models() -> list[dict[str, Any]]:
    """Return list of available AI models."""
    settings = get_settings()
    return [
        {
            "model_id": settings.default_model,
            "provider": "openrouter",
            "display_name": "Grok 4.1 Fast",
        },
        {
            "model_id": "openrouter/anthropic/claude-3.5-sonnet",
            "provider": "openrouter",
            "display_name": "Claude 3.5 Sonnet",
        },
        {
            "model_id": "openrouter/openai/gpt-4o-mini",
            "provider": "openrouter",
            "display_name": "GPT-4o Mini",
        },
    ]


@router.get("/model-roles")
async def get_model_roles() -> dict[str, Any]:
    """Return which model is used for each agent role.

    Resolves empty-string overrides to the effective model ID so the
    frontend can attribute costs to the correct model per role.
    """
    settings = get_settings()
    default = settings.default_model

    return {
        "orchestrator": settings.orchestrator_model or default,
        "scope": settings.scope_model or settings.orchestrator_model or default,
        "sub_explorer": settings.orchestrator_model or default,
        "decomposition": settings.decomposition_model or default,
        "entity_extraction": settings.entity_extraction_model or settings.decomposition_model or default,
        "dimension": settings.dimension_model or default,
        "synthesis": settings.synthesis_model or default,
        "thinking_levels": {
            "orchestrator": settings.orchestrator_thinking_level,
            "decomposition": settings.decomposition_thinking_level,
            "entity_extraction": settings.entity_extraction_thinking_level,
            "file_decomposition": settings.file_decomposition_thinking_level,
            "dimension": settings.dimension_thinking_level,
            "synthesis": settings.synthesis_thinking_level,
            "chat": settings.chat_thinking_level,
        },
    }


@router.get("/filters")
async def get_filters() -> dict[str, Any]:
    """Return filter configuration."""
    return {
        "filters": [],
        "description": "No filters configured yet. Filters control which domains of knowledge each node belongs to.",
    }
