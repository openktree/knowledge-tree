"""Unit tests for SpacyEntityExtractor."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from kt_plugin_be_concept_extractor.strategies.spacy import SpacyEntityExtractor, _is_filler
from tests.conftest import requires_spacy_model


@dataclass
class _Fact:
    content: str
    id: str = "f1"
    fact_type: str = "claim"


def test_is_filler_short() -> None:
    assert _is_filler("a")


def test_is_filler_determiner() -> None:
    assert _is_filler("the")


def test_is_filler_digits() -> None:
    assert _is_filler("2024")


def test_not_filler() -> None:
    assert not _is_filler("NASA")
    assert not _is_filler("general relativity")


@requires_spacy_model
@pytest.mark.asyncio
async def test_extract_returns_entities() -> None:
    extractor = SpacyEntityExtractor()
    facts = [_Fact("NASA launched Apollo 11 in 1969.", id="f1")]
    result = await extractor.extract(facts)
    assert result is not None
    names_lower = {e.name.lower() for e in result}
    assert any("nasa" in n or "apollo" in n for n in names_lower)


@pytest.mark.asyncio
async def test_extract_empty_facts_returns_none() -> None:
    # Avoid touching spaCy model when no facts — guard should short-circuit.
    class _Stub(SpacyEntityExtractor):
        def __init__(self) -> None:  # type: ignore[override]
            pass

    extractor = _Stub()
    result = await extractor.extract([])
    assert result is None


