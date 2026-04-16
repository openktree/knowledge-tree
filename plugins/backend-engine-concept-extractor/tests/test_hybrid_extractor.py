"""Unit tests for HybridEntityExtractor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kt_plugin_be_concept_extractor.strategies.hybrid import (
    HybridEntityExtractor,
    ShellCandidate,
    _is_filler,
    _spacy_pass,
)
from tests.conftest import requires_spacy_model

# ── Helpers ───────────────────────────────────────────────────────────────


@dataclass
class _Fact:
    content: str
    id: str = "test-id-1"
    fact_type: str = "claim"


def _make_gateway(responses: list[dict | None]) -> MagicMock:
    """Gateway whose ``generate_json`` returns successive entries from ``responses``."""
    gw = MagicMock()
    gw.generate_json = AsyncMock(side_effect=responses)
    return gw


def _settings_stub() -> Any:
    s = MagicMock()
    s.shell_model = "test-shell-model"
    s.shell_thinking_level = "minimal"
    s.shell_batch_size = 40
    s.shell_concurrency = 5
    s.alias_model = "test-alias-model"
    s.alias_thinking_level = "minimal"
    s.alias_batch_size = 40
    s.alias_concurrency = 5
    return s


def _mock_candidate(name: str, *, ner_label: str | None, source: str) -> Any:
    cand = MagicMock()
    cand.name = name
    cand.ner_label = ner_label
    cand.source = source
    cand.fact_indices = [1]
    cand.fact_ids = ["f1"]
    return cand


# ── _is_filler ────────────────────────────────────────────────────────────


def test_is_filler_short() -> None:
    assert _is_filler("a")


def test_is_filler_single_pronoun() -> None:
    assert _is_filler("the")
    assert _is_filler("some")


def test_is_filler_all_filler_words() -> None:
    assert _is_filler("the most")


def test_is_filler_digits() -> None:
    assert _is_filler("123")


def test_not_filler() -> None:
    assert not _is_filler("NASA")
    assert not _is_filler("CRISPR")
    assert not _is_filler("general relativity")


# ── _spacy_pass ───────────────────────────────────────────────────────────


@requires_spacy_model
def test_spacy_pass_returns_candidates() -> None:
    facts = [_Fact("NASA launched Apollo 11 in 1969.", id="f1")]
    candidates = _spacy_pass(facts)
    names = {c.name for c in candidates}
    assert any("nasa" in n.lower() or "apollo" in n.lower() for n in names)


@requires_spacy_model
def test_spacy_pass_merges_duplicate_mentions() -> None:
    facts = [
        _Fact("Einstein published special relativity.", id="f1"),
        _Fact("Einstein later worked on general relativity.", id="f2"),
    ]
    candidates = _spacy_pass(facts)
    einstein_cands = [c for c in candidates if "einstein" in c.name.lower()]
    assert len(einstein_cands) == 1
    assert len(einstein_cands[0].fact_indices) == 2


def test_spacy_pass_empty_facts() -> None:
    assert _spacy_pass([]) == []


# ── HybridEntityExtractor ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_extract_returns_kept_entities_with_aliases() -> None:
    # First call: shell classifier (both kept). Second call: alias generator.
    gw = _make_gateway(
        [
            {
                "results": [
                    {"index": 1, "is_shell": False},
                    {"index": 2, "is_shell": False},
                ]
            },
            {
                "results": [
                    {"index": 1, "aliases": ["National Aeronautics and Space Administration"]},
                    {"index": 2, "aliases": []},
                ]
            },
        ]
    )

    cands = [
        _mock_candidate("NASA", ner_label="ORG", source="ner"),
        _mock_candidate("Apollo 11", ner_label="PRODUCT", source="ner"),
    ]
    with (
        patch(
            "kt_plugin_be_concept_extractor.settings.get_concept_extractor_settings",
            return_value=_settings_stub(),
        ),
        patch(
            "kt_plugin_be_concept_extractor.strategies.hybrid._spacy_pass",
            return_value=cands,
        ),
    ):
        extractor = HybridEntityExtractor(gw)
        result = await extractor.extract([_Fact("NASA launched Apollo 11.", id="f1")])

    assert result is not None
    by_name = {e.name: e for e in result}
    assert "NASA" in by_name
    assert "Apollo 11" in by_name
    assert by_name["NASA"].aliases == ["National Aeronautics and Space Administration"]
    assert extractor.get_last_shells() == []


@pytest.mark.asyncio
async def test_extract_separates_shells() -> None:
    gw = _make_gateway(
        [
            # Shell verdict: NASA kept, approach rejected
            {
                "results": [
                    {"index": 1, "is_shell": False},
                    {"index": 2, "is_shell": True},
                ]
            },
            # Alias pass only receives NASA
            {"results": [{"index": 1, "aliases": []}]},
        ]
    )

    cands = [
        _mock_candidate("NASA", ner_label="ORG", source="ner"),
        _mock_candidate("approach", ner_label=None, source="chunk"),
    ]
    with (
        patch(
            "kt_plugin_be_concept_extractor.settings.get_concept_extractor_settings",
            return_value=_settings_stub(),
        ),
        patch(
            "kt_plugin_be_concept_extractor.strategies.hybrid._spacy_pass",
            return_value=cands,
        ),
    ):
        extractor = HybridEntityExtractor(gw)
        kept = await extractor.extract([_Fact("NASA launched its approach.", id="f1")])

    assert kept is not None
    assert [e.name for e in kept] == ["NASA"]

    shells = extractor.get_last_shells()
    assert shells is not None and len(shells) == 1
    assert isinstance(shells[0], ShellCandidate)
    assert shells[0].name == "approach"
    assert shells[0].source == "chunk"


@pytest.mark.asyncio
async def test_extract_raises_on_shell_llm_error() -> None:
    """Shell LLM failure must propagate (fail-fast principle)."""
    gw = _make_gateway([None, {"results": []}])

    cands = [_mock_candidate("NASA", ner_label="ORG", source="ner")]
    with (
        patch(
            "kt_plugin_be_concept_extractor.settings.get_concept_extractor_settings",
            return_value=_settings_stub(),
        ),
        patch(
            "kt_plugin_be_concept_extractor.strategies.hybrid._spacy_pass",
            return_value=cands,
        ),
        pytest.raises(ValueError, match="Expected dict from LLM"),
    ):
        extractor = HybridEntityExtractor(gw)
        await extractor.extract([_Fact("NASA launched.", id="f1")])


@pytest.mark.asyncio
async def test_extract_empty_facts() -> None:
    gw = _make_gateway([])
    extractor = HybridEntityExtractor(gw)
    result = await extractor.extract([])
    assert result == []


# ── Plugin declaration ────────────────────────────────────────────────────


def test_plugin_entry_points() -> None:
    from kt_plugin_be_concept_extractor.plugin import ConceptExtractorBackendEnginePlugin

    plugin = ConceptExtractorBackendEnginePlugin()
    assert plugin.plugin_id == "backend-engine-concept-extractor"

    db = plugin.get_database()
    assert db is not None
    assert db.schema_name == "plugin_hybrid_extractor"
    assert db.alembic_config_path.name == "alembic_hybrid.ini"

    # Three strategies exposed as separate contributions
    names = {c.extractor_name for c in plugin.get_entity_extractors()}
    assert names == {"spacy", "llm", "hybrid"}

    hooks = list(plugin.get_post_extraction_hooks())
    assert len(hooks) == 1
    assert hooks[0].extractor_name == "hybrid"
    assert hooks[0].output_key == "shells"
