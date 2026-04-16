"""Tests for LLM entity extraction — ported from kt-facts."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from kt_plugin_be_concept_extractor.strategies.llm_extraction import extract_entities_from_facts


def _gateway(response: dict | None = None, *, side_effect=None) -> MagicMock:
    gw = MagicMock()
    gw.entity_extraction_model = "test-model"
    gw.entity_extraction_thinking_level = ""
    if side_effect is not None:
        gw.generate_json = AsyncMock(side_effect=side_effect)
    else:
        gw.generate_json = AsyncMock(return_value=response)
    return gw


@pytest.mark.asyncio
async def test_extract_entities_empty_list() -> None:
    gateway = MagicMock()
    result = await extract_entities_from_facts([], gateway)
    assert result is None


@pytest.mark.asyncio
async def test_extract_entities_valid_response() -> None:
    facts = [
        MagicMock(fact_type="claim", content="Einstein developed general relativity in 1915"),
        MagicMock(fact_type="claim", content="NASA launched Apollo 11 in 1969"),
    ]
    gateway = _gateway(
        {
            "facts": {
                "1": [
                    {"name": "Albert Einstein", "node_type": "entity", "entity_subtype": "person", "aliases": []},
                    {"name": "general relativity", "node_type": "concept", "aliases": []},
                ],
                "2": [
                    {"name": "NASA", "node_type": "entity", "entity_subtype": "organization", "aliases": []},
                    {"name": "Apollo 11 launch", "node_type": "event", "aliases": []},
                ],
            }
        }
    )
    result = await extract_entities_from_facts(facts, gateway, scope="physics history")
    assert result is not None
    assert len(result) == 4
    names = {r["name"] for r in result}
    assert {"Albert Einstein", "general relativity", "NASA", "Apollo 11 launch"} <= names


@pytest.mark.asyncio
async def test_extract_entities_normalizes_invalid_type() -> None:
    facts = [MagicMock(fact_type="claim", content="test fact")]
    gateway = _gateway(
        {"facts": {"1": [{"name": "test node", "node_type": "invalid_type", "aliases": []}]}}
    )
    result = await extract_entities_from_facts(facts, gateway)
    assert result is not None
    assert result[0]["node_type"] == "concept"


@pytest.mark.asyncio
async def test_extract_entities_skips_invalid_entries() -> None:
    facts = [MagicMock(fact_type="claim", content="test fact")]
    gateway = _gateway(
        {
            "facts": {
                "1": [
                    {"name": "valid node", "node_type": "concept", "aliases": []},
                    {"name": "", "node_type": "concept", "aliases": []},
                    {"node_type": "entity"},
                    "not a dict",
                    {"name": "another valid", "node_type": "entity", "entity_subtype": "other", "aliases": []},
                ]
            }
        }
    )
    result = await extract_entities_from_facts(facts, gateway)
    assert result is not None
    assert len(result) == 2
    names = {r["name"] for r in result}
    assert {"valid node", "another valid"} <= names


@pytest.mark.asyncio
async def test_extract_entities_returns_none_on_failure() -> None:
    facts = [MagicMock(fact_type="claim", content="test fact")]
    gateway = _gateway(side_effect=RuntimeError("LLM error"))
    result = await extract_entities_from_facts(facts, gateway)
    assert result is None


@pytest.mark.asyncio
async def test_extract_entities_returns_none_on_empty_response() -> None:
    facts = [MagicMock(fact_type="claim", content="test fact")]
    gateway = _gateway(None)
    result = await extract_entities_from_facts(facts, gateway)
    assert result is None


@pytest.mark.asyncio
async def test_extract_entities_returns_none_on_missing_facts_key() -> None:
    facts = [MagicMock(fact_type="claim", content="test fact")]
    gateway = _gateway({"something_else": "value"})
    result = await extract_entities_from_facts(facts, gateway)
    assert result is None


@pytest.mark.asyncio
async def test_extract_entities_entity_subtype() -> None:
    facts = [MagicMock(fact_type="claim", content="test")]
    gateway = _gateway(
        {
            "facts": {
                "1": [
                    {"name": "Einstein", "node_type": "entity", "entity_subtype": "person", "aliases": []},
                    {"name": "NASA", "node_type": "entity", "entity_subtype": "organization", "aliases": []},
                ]
            }
        }
    )
    result = await extract_entities_from_facts(facts, gateway)
    assert result is not None
    einstein = next(r for r in result if r["name"] == "Einstein")
    nasa = next(r for r in result if r["name"] == "NASA")
    assert einstein["entity_subtype"] == "person"
    assert nasa["entity_subtype"] == "organization"


@pytest.mark.asyncio
async def test_extract_entities_batching() -> None:
    facts = [MagicMock(fact_type="claim", content=f"fact {i}") for i in range(3)]
    call_count = 0

    async def mock_generate(**kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"facts": {"1": [{"name": "node A", "node_type": "concept", "aliases": []}]}}
        return {"facts": {"3": [{"name": "node A", "node_type": "concept", "aliases": []}]}}

    gateway = _gateway(side_effect=mock_generate)
    result = await extract_entities_from_facts(facts, gateway, batch_size=2)
    assert result is not None
    assert len(result) == 1
    assert gateway.generate_json.call_count == 2


@pytest.mark.asyncio
async def test_extract_entities_merges_fact_indices_across_batches() -> None:
    facts = [MagicMock(fact_type="claim", content=f"fact {i}") for i in range(4)]
    call_count = 0

    async def mock_generate(**kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {
                "facts": {
                    "1": [{"name": "Einstein", "node_type": "entity", "entity_subtype": "person", "aliases": []}],
                    "2": [{"name": "Einstein", "node_type": "entity", "entity_subtype": "person", "aliases": []}],
                }
            }
        return {
            "facts": {
                "3": [{"name": "Einstein", "node_type": "entity", "entity_subtype": "person", "aliases": []}],
                "4": [{"name": "Einstein", "node_type": "entity", "entity_subtype": "person", "aliases": []}],
            }
        }

    gateway = _gateway(side_effect=mock_generate)
    result = await extract_entities_from_facts(facts, gateway, batch_size=2)
    assert result is not None
    assert len(result) == 1
    assert sorted(result[0]["fact_indices"]) == [1, 2, 3, 4]


# ── is_valid_entity_name integration ──────────────────────────────────────


@pytest.mark.asyncio
async def test_extraction_filters_initials() -> None:
    facts = [MagicMock(fact_type="claim", content="test fact")]
    gateway = _gateway(
        {
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
    assert [r["name"] for r in result] == ["Albert Einstein"]


@pytest.mark.asyncio
async def test_extraction_filters_et_al() -> None:
    facts = [MagicMock(fact_type="claim", content="test fact")]
    gateway = _gateway(
        {
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
    assert [r["name"] for r in result] == ["quantum mechanics"]


@pytest.mark.asyncio
async def test_extraction_returns_none_when_all_filtered() -> None:
    facts = [MagicMock(fact_type="claim", content="test fact")]
    gateway = _gateway(
        {
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


