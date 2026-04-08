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
            "model_id": "openrouter/anthropic/claude-sonnet-4.6",
            "provider": "openrouter",
            "display_name": "Claude Sonnet 4.6",
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


# Curated allowlist of models available for user-selected synthesis.
# Manually maintained — update when OpenRouter model IDs change or models
# are added/retired.  To make this configurable without a code change,
# move the list to Settings and populate from env/YAML.
SYNTHESIS_MODELS: list[dict[str, str]] = [
    {"model_id": "openrouter/google/gemini-3.1-pro-preview", "display_name": "Gemini 3.1 Pro", "provider": "google"},
    {"model_id": "openrouter/z-ai/glm-5v-turbo", "display_name": "GLM 5 Turbo", "provider": "z-ai"},
    {"model_id": "openrouter/minimax/minimax-2.7", "display_name": "MiniMax 2.7", "provider": "minimax"},
    {"model_id": "openrouter/anthropic/claude-sonnet-4", "display_name": "Claude Sonnet", "provider": "anthropic"},
    {"model_id": "openrouter/anthropic/claude-opus-4", "display_name": "Claude Opus", "provider": "anthropic"},
    {"model_id": "openrouter/deepseek/deepseek-v4", "display_name": "DeepSeek V4", "provider": "deepseek"},
]

SYNTHESIS_MODEL_IDS: set[str] = {m["model_id"] for m in SYNTHESIS_MODELS}


@router.get("/synthesis-models")
async def get_synthesis_models() -> list[dict[str, str]]:
    """Return the curated list of models available for user-selected synthesis.

    This is separate from ``GET /config/models`` which lists system-level
    models used across all agent roles.  The synthesis list is a smaller,
    curated subset of high-quality models that are suitable for long-form
    document generation and that we want to expose to end-users.
    """
    return SYNTHESIS_MODELS


@router.get("/filters")
async def get_filters() -> dict[str, Any]:
    """Return filter configuration."""
    return {
        "filters": [],
        "description": "No filters configured yet. Filters control which domains of knowledge each node belongs to.",
    }
