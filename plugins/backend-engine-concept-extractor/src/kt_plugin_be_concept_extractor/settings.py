"""Plugin-local settings for the concept-extractor plugin.

Each plugin owns its own ``BaseSettings`` subclass so the core
``kt_config.Settings`` class does not grow a field for every plugin knob.

Env prefix: ``CONCEPT_EXTRACTOR_`` (e.g. ``CONCEPT_EXTRACTOR_SHELL_MODEL``).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _project_root() -> Path:
    """Walk up until we find the workspace ``.env`` (best effort)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".env").is_file():
            return parent
    # Fallback: repo root by convention
    return here.parents[4]


class ConceptExtractorSettings(BaseSettings):
    """Runtime knobs for the hybrid / spaCy / LLM concept extractors."""

    model_config = SettingsConfigDict(
        env_prefix="CONCEPT_EXTRACTOR_",
        env_file=str(_project_root() / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Hybrid — shell classifier
    shell_model: str = "openrouter/google/gemma-4-26b-a4b-it:nitro"
    shell_thinking_level: str = ""
    shell_batch_size: int = 40
    shell_concurrency: int = 5

    # Hybrid — alias generator
    alias_model: str = "openrouter/google/gemini-3-flash-preview"
    alias_thinking_level: str = "minimal"
    alias_batch_size: int = 40
    alias_concurrency: int = 5


@lru_cache(maxsize=1)
def get_concept_extractor_settings() -> ConceptExtractorSettings:
    """Return the cached plugin settings singleton."""
    return ConceptExtractorSettings()
