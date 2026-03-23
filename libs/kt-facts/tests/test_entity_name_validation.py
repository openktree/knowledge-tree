"""Tests for _is_valid_entity_name and its integration with extraction."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from kt_facts.processing.entity_extraction import (
    _is_valid_entity_name,
    extract_entities_from_facts,
)

# ── _is_valid_entity_name unit tests ──────────────────────────────


class TestIsValidEntityName:
    """Unit tests for the name validation function."""

    def test_valid_names(self) -> None:
        assert _is_valid_entity_name("Albert Einstein") is True
        assert _is_valid_entity_name("NASA") is True
        assert _is_valid_entity_name("CRISPR-Cas9") is True
        assert _is_valid_entity_name("World Health Organization") is True
        assert _is_valid_entity_name("quantum entanglement") is True
        assert _is_valid_entity_name("FBI") is True
        assert _is_valid_entity_name("2008 financial crisis") is True
        assert _is_valid_entity_name("Jennifer Doudna") is True
        assert _is_valid_entity_name("Ab") is True  # 2 chars minimum

    def test_reject_too_short(self) -> None:
        assert _is_valid_entity_name("") is False
        assert _is_valid_entity_name("A") is False

    def test_reject_too_long(self) -> None:
        assert _is_valid_entity_name("x" * 151) is False

    def test_reject_pure_initials(self) -> None:
        assert _is_valid_entity_name("K. M. A.") is False
        assert _is_valid_entity_name("J. R. R.") is False
        assert _is_valid_entity_name("A B C D") is False
        assert _is_valid_entity_name("K M A M H") is False

    def test_reject_et_al(self) -> None:
        assert _is_valid_entity_name("Smith et al.") is False
        assert _is_valid_entity_name("K.M.A. et al.") is False
        assert _is_valid_entity_name("Silva et al. (2020)") is False

    def test_reject_low_alpha_ratio(self) -> None:
        assert _is_valid_entity_name("... --- ...") is False
        assert _is_valid_entity_name("1234567890") is False
        assert _is_valid_entity_name("(((((())))))") is False

    def test_reject_repeated_patterns(self) -> None:
        # "K. M. A. " repeated 3+ times
        assert _is_valid_entity_name("K. M. A. K. M. A. K. M. A.") is False
        # Repeated short pattern
        assert _is_valid_entity_name("abc abc abc abc abc") is False

    def test_accept_legitimate_repeated_words(self) -> None:
        # Real names shouldn't trigger the repeat detector
        assert _is_valid_entity_name("New York City") is True
        assert _is_valid_entity_name("World Wide Web") is True

    def test_accept_known_acronyms(self) -> None:
        # Short all-caps should be valid (not pure single-letter tokens)
        assert _is_valid_entity_name("FBI") is True
        assert _is_valid_entity_name("WHO") is True
        assert _is_valid_entity_name("UNESCO") is True


# ── Integration: _parse_per_fact_result filters junk ──────────────


@pytest.mark.asyncio
async def test_extraction_filters_initials() -> None:
    """LLM output with initials-only names should be filtered."""
    facts = [MagicMock(fact_type="claim", content="test fact")]
    gateway = MagicMock()
    gateway.decomposition_model = "test-model"
    gateway.generate_json = AsyncMock(
        return_value={
            "facts": {
                "1": [
                    {"name": "K. M. A.", "node_type": "entity", "entity_subtype": "person", "aliases": []},
                    {"name": "Albert Einstein", "node_type": "entity", "entity_subtype": "person", "aliases": []},
                ]
            }
        }
    )

    result = await extract_entities_from_facts(facts, gateway)
    assert result is not None
    assert len(result) == 1
    assert result[0]["name"] == "Albert Einstein"


@pytest.mark.asyncio
async def test_extraction_filters_et_al() -> None:
    """Citation artifacts with 'et al.' should be filtered."""
    facts = [MagicMock(fact_type="claim", content="test fact")]
    gateway = MagicMock()
    gateway.decomposition_model = "test-model"
    gateway.generate_json = AsyncMock(
        return_value={
            "facts": {
                "1": [
                    {"name": "Smith et al.", "node_type": "entity", "entity_subtype": "person", "aliases": []},
                    {"name": "quantum mechanics", "node_type": "concept", "aliases": []},
                ]
            }
        }
    )

    result = await extract_entities_from_facts(facts, gateway)
    assert result is not None
    assert len(result) == 1
    assert result[0]["name"] == "quantum mechanics"


@pytest.mark.asyncio
async def test_extraction_filters_repeated_patterns() -> None:
    """Repeated substring patterns should be filtered."""
    facts = [MagicMock(fact_type="claim", content="test fact")]
    gateway = MagicMock()
    gateway.decomposition_model = "test-model"
    gateway.generate_json = AsyncMock(
        return_value={
            "facts": {
                "1": [
                    {
                        "name": "K. M. A. M. H. K. M. A. M. H. K. M. A. M. H.",
                        "node_type": "entity",
                        "entity_subtype": "person",
                        "aliases": [],
                    },
                    {"name": "gene therapy", "node_type": "concept", "aliases": []},
                ]
            }
        }
    )

    result = await extract_entities_from_facts(facts, gateway)
    assert result is not None
    assert len(result) == 1
    assert result[0]["name"] == "gene therapy"


@pytest.mark.asyncio
async def test_extraction_returns_none_when_all_filtered() -> None:
    """If all entities are junk, return None."""
    facts = [MagicMock(fact_type="claim", content="test fact")]
    gateway = MagicMock()
    gateway.decomposition_model = "test-model"
    gateway.generate_json = AsyncMock(
        return_value={
            "facts": {
                "1": [
                    {"name": "K. M. A.", "node_type": "entity", "entity_subtype": "person", "aliases": []},
                    {"name": "et al. (2020)", "node_type": "entity", "entity_subtype": "person", "aliases": []},
                ]
            }
        }
    )

    result = await extract_entities_from_facts(facts, gateway)
    assert result is None
