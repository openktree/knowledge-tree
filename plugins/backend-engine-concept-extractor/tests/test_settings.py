"""Verify plugin-local settings load defaults correctly."""

from __future__ import annotations

from kt_plugin_be_concept_extractor.settings import (
    ConceptExtractorSettings,
    get_concept_extractor_settings,
)


def test_defaults_match_expected():
    s = ConceptExtractorSettings()
    assert s.shell_model.startswith("openrouter/google/gemma-4-26b-a4b-it")
    assert s.shell_thinking_level == ""
    assert s.shell_batch_size == 40
    assert s.shell_concurrency == 5
    assert "gemini-3-flash-preview" in s.alias_model


def test_get_settings_cached():
    a = get_concept_extractor_settings()
    b = get_concept_extractor_settings()
    assert a is b


def test_env_prefix(monkeypatch):
    monkeypatch.setenv("CONCEPT_EXTRACTOR_SHELL_BATCH_SIZE", "99")
    s = ConceptExtractorSettings()
    assert s.shell_batch_size == 99
