"""Tests for entity extraction from facts."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from kt_facts.processing.entity_extraction import extract_entities_from_facts


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

    gateway = MagicMock()
    gateway.decomposition_model = "test-model"
    gateway.generate_json = AsyncMock(return_value={
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
    })

    result = await extract_entities_from_facts(facts, gateway, scope="physics history")
    assert result is not None
    assert len(result) == 4
    names = {r["name"] for r in result}
    assert "Albert Einstein" in names
    assert "general relativity" in names
    assert "NASA" in names
    assert "Apollo 11 launch" in names
    einstein = next(r for r in result if r["name"] == "Albert Einstein")
    assert einstein["node_type"] == "entity"
    relativity = next(r for r in result if r["name"] == "general relativity")
    assert relativity["node_type"] == "concept"


@pytest.mark.asyncio
async def test_extract_entities_normalizes_invalid_type() -> None:
    facts = [MagicMock(fact_type="claim", content="test fact")]
    gateway = MagicMock()
    gateway.decomposition_model = "test-model"
    gateway.generate_json = AsyncMock(return_value={
        "facts": {
            "1": [
                {"name": "test node", "node_type": "invalid_type", "aliases": []},
            ]
        }
    })

    result = await extract_entities_from_facts(facts, gateway)
    assert result is not None
    assert len(result) == 1
    assert result[0]["node_type"] == "concept"  # normalized to concept


@pytest.mark.asyncio
async def test_extract_entities_skips_invalid_entries() -> None:
    facts = [MagicMock(fact_type="claim", content="test fact")]
    gateway = MagicMock()
    gateway.decomposition_model = "test-model"
    gateway.generate_json = AsyncMock(return_value={
        "facts": {
            "1": [
                {"name": "valid node", "node_type": "concept", "aliases": []},
                {"name": "", "node_type": "concept", "aliases": []},  # empty name
                {"node_type": "entity"},  # missing name
                "not a dict",  # invalid type
                {"name": "another valid", "node_type": "entity", "entity_subtype": "other", "aliases": []},
            ]
        }
    })

    result = await extract_entities_from_facts(facts, gateway)
    assert result is not None
    assert len(result) == 2
    names = {r["name"] for r in result}
    assert "valid node" in names
    assert "another valid" in names


@pytest.mark.asyncio
async def test_extract_entities_returns_none_on_failure() -> None:
    facts = [MagicMock(fact_type="claim", content="test fact")]
    gateway = MagicMock()
    gateway.decomposition_model = "test-model"
    gateway.generate_json = AsyncMock(side_effect=RuntimeError("LLM error"))

    result = await extract_entities_from_facts(facts, gateway)
    assert result is None


@pytest.mark.asyncio
async def test_extract_entities_returns_none_on_empty_response() -> None:
    facts = [MagicMock(fact_type="claim", content="test fact")]
    gateway = MagicMock()
    gateway.decomposition_model = "test-model"
    gateway.generate_json = AsyncMock(return_value=None)

    result = await extract_entities_from_facts(facts, gateway)
    assert result is None


@pytest.mark.asyncio
async def test_extract_entities_returns_none_on_missing_facts_key() -> None:
    facts = [MagicMock(fact_type="claim", content="test fact")]
    gateway = MagicMock()
    gateway.decomposition_model = "test-model"
    gateway.generate_json = AsyncMock(return_value={"something_else": "value"})

    result = await extract_entities_from_facts(facts, gateway)
    assert result is None


@pytest.mark.asyncio
async def test_extract_entities_entity_subtype() -> None:
    facts = [MagicMock(fact_type="claim", content="test")]
    gateway = MagicMock()
    gateway.decomposition_model = "test-model"
    gateway.generate_json = AsyncMock(return_value={
        "facts": {
            "1": [
                {"name": "Einstein", "node_type": "entity", "entity_subtype": "person", "aliases": []},
                {"name": "NASA", "node_type": "entity", "entity_subtype": "organization", "aliases": []},
            ]
        }
    })

    result = await extract_entities_from_facts(facts, gateway)
    assert result is not None
    einstein = next(r for r in result if r["name"] == "Einstein")
    nasa = next(r for r in result if r["name"] == "NASA")
    assert einstein["entity_subtype"] == "person"
    assert nasa["entity_subtype"] == "organization"


@pytest.mark.asyncio
async def test_extract_entities_batching() -> None:
    """With batch_size=2 and 3 facts, should make 2 batches."""
    facts = [
        MagicMock(fact_type="claim", content=f"fact {i}")
        for i in range(3)
    ]
    gateway = MagicMock()
    gateway.decomposition_model = "test-model"

    call_count = 0

    async def mock_generate(**kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Batch 1: facts 1-2
            return {"facts": {"1": [{"name": "node A", "node_type": "concept", "aliases": []}]}}
        # Batch 2: fact 3
        return {"facts": {"3": [{"name": "node A", "node_type": "concept", "aliases": []}]}}

    gateway.generate_json = AsyncMock(side_effect=mock_generate)

    result = await extract_entities_from_facts(facts, gateway, batch_size=2)
    assert result is not None
    # 2 batches, each returns "node A" — should deduplicate
    assert len(result) == 1
    assert gateway.generate_json.call_count == 2


@pytest.mark.asyncio
async def test_extract_entities_merges_fact_indices_across_batches() -> None:
    """Same node in different batches should have merged fact_indices."""
    facts = [
        MagicMock(fact_type="claim", content=f"fact {i}")
        for i in range(4)
    ]
    gateway = MagicMock()
    gateway.decomposition_model = "test-model"

    call_count = 0

    async def mock_generate(**kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Batch 1: facts 1-2
            return {"facts": {
                "1": [{"name": "Einstein", "node_type": "entity", "entity_subtype": "person", "aliases": []}],
                "2": [{"name": "Einstein", "node_type": "entity", "entity_subtype": "person", "aliases": []}],
            }}
        # Batch 2: facts 3-4
        return {"facts": {
            "3": [{"name": "Einstein", "node_type": "entity", "entity_subtype": "person", "aliases": []}],
            "4": [{"name": "Einstein", "node_type": "entity", "entity_subtype": "person", "aliases": []}],
        }}

    gateway.generate_json = AsyncMock(side_effect=mock_generate)

    result = await extract_entities_from_facts(facts, gateway, batch_size=2)
    assert result is not None
    assert len(result) == 1
    assert sorted(result[0]["fact_indices"]) == [1, 2, 3, 4]
