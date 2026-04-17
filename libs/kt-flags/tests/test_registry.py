"""Registry-level invariants: naming, typing, Settings mappings."""

from __future__ import annotations

import pytest

from kt_config.settings import Settings
from kt_flags.registry import (
    _KEY_REGEX,
    ALLOWED_SECTIONS,
    FLAG_REGISTRY,
    FlagSpec,
    FlagType,
)


def test_every_key_matches_naming_convention() -> None:
    for key in FLAG_REGISTRY:
        assert _KEY_REGEX.match(key), f"{key!r} does not match naming regex"


def test_every_key_uses_allowed_section() -> None:
    for spec in FLAG_REGISTRY.values():
        assert spec.section in ALLOWED_SECTIONS


def test_every_settings_field_exists_on_settings() -> None:
    fields = set(Settings.model_fields.keys())
    for spec in FLAG_REGISTRY.values():
        if spec.settings_field is None:
            continue
        assert spec.settings_field in fields, f"{spec.key}: settings_field {spec.settings_field!r} not on Settings"


def test_invalid_key_rejected() -> None:
    with pytest.raises(ValueError):
        FlagSpec(
            key="not_a_valid.section",
            type=FlagType.BOOLEAN,
            default=True,
            description="",
        )


def test_phase0_registry_has_representative_flags() -> None:
    assert "feature.full_text_fetch" in FLAG_REGISTRY
    assert "plugin.concept_extractor.enabled" in FLAG_REGISTRY
    assert "provider.serper.enabled" in FLAG_REGISTRY
