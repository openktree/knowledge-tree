"""Plugin-local settings for the concept-extractor plugin.

Reads from config.yaml (section ``concept_extractor``), .env, and
environment variables.  Priority: env vars > .env > config.yaml > defaults.

Env prefix: ``CONCEPT_EXTRACTOR_`` (e.g. ``CONCEPT_EXTRACTOR_SHELL_MODEL``).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


def _project_root() -> Path:
    """Walk up until we find the workspace ``.env`` (best effort)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".env").is_file():
            return parent
    return here.parents[4]


_YAML_SECTION = "concept_extractor"


class _YamlSource(PydanticBaseSettingsSource):
    """Read plugin knobs from the ``concept_extractor`` section of config.yaml."""

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        self._data: dict[str, Any] = {}
        yaml_path = os.environ.get("CONFIG_PATH", str(_project_root() / "config.yaml"))
        p = Path(yaml_path)
        if p.is_file():
            with open(p) as f:
                raw = yaml.safe_load(f)
            if isinstance(raw, dict):
                section = raw.get(_YAML_SECTION)
                if isinstance(section, dict):
                    self._data = section

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        val = self._data.get(field_name)
        return val, field_name, self.field_is_complex(field)

    def __call__(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        for field_name, field_info in self.settings_cls.model_fields.items():
            val, _, _ = self.get_field_value(field_info, field_name)
            if val is not None:
                d[field_name] = val
        return d


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

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            _YamlSource(settings_cls),
            file_secret_settings,
        )


@lru_cache(maxsize=1)
def get_concept_extractor_settings() -> ConceptExtractorSettings:
    """Return the cached plugin settings singleton."""
    return ConceptExtractorSettings()
