"""Unit tests for HybridEntityExtractor."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kt_plugin_be_hybrid_extractor.extractor import (
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


def _make_gateway(validation_response: dict | None = None) -> MagicMock:
    gw = MagicMock()
    gw.generate_json = AsyncMock(return_value=validation_response)
    return gw


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
    # spaCy should find at least NASA or Apollo
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
async def test_extract_returns_kept_entities() -> None:
    gw = _make_gateway(
        {
            "results": [
                {"name": "NASA", "keep": True, "aliases": ["National Aeronautics and Space Administration"]},
                {"name": "Apollo 11", "keep": True, "aliases": []},
            ]
        }
    )

    with patch("kt_config.settings.get_settings") as mock_settings:
        mock_settings.return_value.hybrid_extractor_validation_model = "test-model"
        mock_settings.return_value.hybrid_extractor_validation_batch_size = 50

        extractor = HybridEntityExtractor(gw)

        cand1 = MagicMock()
        cand1.name = "NASA"
        cand1.ner_label = "ORG"
        cand1.source = "ner"
        cand1.fact_indices = [1]
        cand1.fact_ids = ["f1"]
        cand2 = MagicMock()
        cand2.name = "Apollo 11"
        cand2.ner_label = "PRODUCT"
        cand2.source = "ner"
        cand2.fact_indices = [1]
        cand2.fact_ids = ["f1"]
        with patch(
            "kt_plugin_be_hybrid_extractor.extractor._spacy_pass",
            return_value=[cand1, cand2],
        ):
            result = await extractor.extract([_Fact("NASA launched Apollo 11.", id="f1")])

    assert result is not None
    names = {e.name for e in result}
    assert "NASA" in names
    assert "Apollo 11" in names


@pytest.mark.asyncio
async def test_extract_with_shells_separates_rejected() -> None:
    gw = _make_gateway(
        {
            "results": [
                {"name": "NASA", "keep": True, "aliases": []},
                {"name": "approach", "keep": False, "aliases": []},
            ]
        }
    )

    with patch("kt_config.settings.get_settings") as mock_settings:
        mock_settings.return_value.hybrid_extractor_validation_model = "test-model"
        mock_settings.return_value.hybrid_extractor_validation_batch_size = 50

        extractor = HybridEntityExtractor(gw)

        cand1 = MagicMock()
        cand1.name = "NASA"
        cand1.ner_label = "ORG"
        cand1.source = "ner"
        cand1.fact_indices = [1]
        cand1.fact_ids = ["f1"]
        cand2 = MagicMock()
        cand2.name = "approach"
        cand2.ner_label = None
        cand2.source = "chunk"
        cand2.fact_indices = [1]
        cand2.fact_ids = ["f1"]
        with patch(
            "kt_plugin_be_hybrid_extractor.extractor._spacy_pass",
            return_value=[cand1, cand2],
        ):
            kept, shells = await extractor.extract_with_shells(
                [_Fact("NASA launched its approach.", id="f1")]
            )

    assert len(kept) == 1
    assert kept[0].name == "NASA"
    assert len(shells) == 1
    assert shells[0].name == "approach"
    assert isinstance(shells[0], ShellCandidate)


@pytest.mark.asyncio
async def test_extract_fail_open_on_llm_error() -> None:
    """When LLM fails, extractor keeps all spaCy candidates (fail open)."""
    gw = _make_gateway(None)  # LLM returns None

    with patch("kt_config.settings.get_settings") as mock_settings:
        mock_settings.return_value.hybrid_extractor_validation_model = "test-model"
        mock_settings.return_value.hybrid_extractor_validation_batch_size = 50

        extractor = HybridEntityExtractor(gw)

        candidate = MagicMock()
        candidate.name = "NASA"
        candidate.ner_label = "ORG"
        candidate.source = "ner"
        candidate.fact_indices = [1]
        candidate.fact_ids = ["f1"]

        with patch(
            "kt_plugin_be_hybrid_extractor.extractor._spacy_pass",
            return_value=[candidate],
        ):
            kept, shells = await extractor.extract_with_shells(
                [_Fact("NASA launched.", id="f1")]
            )

    # Fail open: candidate kept, no shells
    assert len(kept) == 1
    assert len(shells) == 0


@pytest.mark.asyncio
async def test_extract_empty_facts() -> None:
    gw = _make_gateway()
    extractor = HybridEntityExtractor(gw)
    result = await extractor.extract([])
    assert result is None


# ── Plugin declaration ────────────────────────────────────────────────────


def test_plugin_entry_points() -> None:
    from kt_plugin_be_hybrid_extractor.plugin import HybridExtractorBackendEnginePlugin

    plugin = HybridExtractorBackendEnginePlugin()
    assert plugin.plugin_id == "backend-engine-hybrid-extractor"

    db = plugin.get_database()
    assert db is not None
    assert db.schema_name == "plugin_hybrid_extractor"
    assert db.alembic_config_path.name == "alembic_hybrid.ini"

    contrib = plugin.get_entity_extractor()
    assert contrib is not None
    assert contrib.extractor_name == "hybrid"
