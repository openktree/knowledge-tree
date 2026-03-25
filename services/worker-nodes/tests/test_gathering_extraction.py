"""Tests for node extraction from gathered facts."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kt_facts.processing.entity_extraction import extract_entities_from_facts
from kt_worker_nodes.pipelines.gathering.pipeline import GatherFactsPipeline

# ── extract_entities_from_facts tests ─────────────────────────────


@pytest.mark.asyncio
async def test_extract_nodes_empty_list() -> None:
    gateway = MagicMock()
    result = await extract_entities_from_facts([], gateway)
    assert result is None


@pytest.mark.asyncio
async def test_extract_nodes_valid_response() -> None:
    facts = [
        MagicMock(fact_type="claim", content="Einstein developed general relativity in 1915"),
        MagicMock(fact_type="claim", content="NASA launched Apollo 11 in 1969"),
    ]

    gateway = MagicMock()
    gateway.decomposition_model = "test-model"
    gateway.generate_json = AsyncMock(
        return_value={
            "facts": {
                "1": [
                    {"name": "Albert Einstein", "node_type": "entity"},
                    {"name": "general relativity", "node_type": "concept"},
                ],
                "2": [
                    {"name": "NASA", "node_type": "entity"},
                    {"name": "Apollo 11 launch", "node_type": "event"},
                ],
            }
        }
    )

    result = await extract_entities_from_facts(facts, gateway, scope="physics history")
    assert result is not None
    assert len(result) == 4
    names = {r["name"] for r in result}
    assert "Albert Einstein" in names
    assert "general relativity" in names
    assert "NASA" in names
    assert "Apollo 11 launch" in names


@pytest.mark.asyncio
async def test_extract_nodes_normalizes_invalid_type() -> None:
    facts = [MagicMock(fact_type="claim", content="test fact")]
    gateway = MagicMock()
    gateway.decomposition_model = "test-model"
    gateway.generate_json = AsyncMock(
        return_value={
            "facts": {
                "1": [
                    {"name": "test node", "node_type": "invalid_type"},
                ],
            }
        }
    )

    result = await extract_entities_from_facts(facts, gateway)
    assert result is not None
    assert len(result) == 1
    assert result[0]["node_type"] == "concept"  # normalized to concept


@pytest.mark.asyncio
async def test_extract_nodes_skips_invalid_entries() -> None:
    facts = [MagicMock(fact_type="claim", content="test fact")]
    gateway = MagicMock()
    gateway.decomposition_model = "test-model"
    gateway.generate_json = AsyncMock(
        return_value={
            "facts": {
                "1": [
                    {"name": "valid node", "node_type": "concept"},
                    {"name": "", "node_type": "concept"},  # empty name
                    {"node_type": "entity"},  # missing name
                    "not a dict",  # invalid type
                    {"name": "another valid", "node_type": "entity"},
                ],
            }
        }
    )

    result = await extract_entities_from_facts(facts, gateway)
    assert result is not None
    assert len(result) == 2
    names = [r["name"] for r in result]
    assert "valid node" in names
    assert "another valid" in names


@pytest.mark.asyncio
async def test_extract_nodes_returns_none_on_failure() -> None:
    facts = [MagicMock(fact_type="claim", content="test fact")]
    gateway = MagicMock()
    gateway.decomposition_model = "test-model"
    gateway.generate_json = AsyncMock(side_effect=RuntimeError("LLM error"))

    result = await extract_entities_from_facts(facts, gateway)
    assert result is None


@pytest.mark.asyncio
async def test_extract_nodes_returns_none_on_empty_response() -> None:
    facts = [MagicMock(fact_type="claim", content="test fact")]
    gateway = MagicMock()
    gateway.decomposition_model = "test-model"
    gateway.generate_json = AsyncMock(return_value=None)

    result = await extract_entities_from_facts(facts, gateway)
    assert result is None


@pytest.mark.asyncio
async def test_extract_nodes_returns_none_on_missing_nodes_key() -> None:
    facts = [MagicMock(fact_type="claim", content="test fact")]
    gateway = MagicMock()
    gateway.decomposition_model = "test-model"
    gateway.generate_json = AsyncMock(return_value={"something_else": "value"})

    result = await extract_entities_from_facts(facts, gateway)
    assert result is None


# ── GatherFactsPipeline param tests ─────────────────────────────


@pytest.mark.asyncio
async def test_gather_extraction_mode_includes_extracted_nodes() -> None:
    """When enable_extraction=True, extracted_nodes from decompose() are included."""
    ctx = MagicMock()
    ctx.provider_registry = MagicMock()
    from kt_config.types import RawSearchResult

    search_results = {
        "query1": [RawSearchResult(title="r1", uri="http://r1.com", raw_content="c1", provider_id="test")]
    }
    ctx.provider_registry.search_all = AsyncMock(return_value=search_results)
    ctx.session = MagicMock()
    ctx.session.commit = AsyncMock()
    ctx.session.rollback = AsyncMock()
    ctx.graph_engine._write_session = AsyncMock()
    ctx.write_session_factory = None
    ctx.emit = AsyncMock()

    state = MagicMock()
    state.explore_remaining = 1
    state.explore_used = 0
    state.explore_budget = 1
    state.nav_budget = 0
    state.nav_used = 0
    state.gathered_fact_count = 0
    state.query = "test query"
    state.scope_description = "test scope"

    pipeline = GatherFactsPipeline(ctx)

    # Mock DecompositionResult
    mock_decomp_result = MagicMock()
    mock_decomp_result.facts = [MagicMock()]
    mock_decomp_result.extracted_nodes = [{"name": "test", "node_type": "concept", "fact_indices": [1]}]
    mock_decomp_result.seed_keys = ["seed:concept:test"]

    mock_page_log = MagicMock()
    mock_page_log.check_urls_freshness = AsyncMock(return_value={})
    mock_page_log.record_fetch = AsyncMock()

    mock_settings = MagicMock()
    mock_settings.full_text_fetch_per_budget_point = 10
    mock_settings.fetch_guarantee_max_rounds = 1
    mock_settings.page_stale_days = 30

    mock_source = MagicMock()
    mock_source.is_super_source = False
    mock_source.is_full_text = True
    mock_source.uri = "http://r1.com"
    mock_source.id = "src-1"
    mock_source.raw_content = "c1"
    mock_source.provider_metadata = None
    mock_source.content_type = None

    with (
        patch(
            "kt_worker_nodes.pipelines.gathering.pipeline._summarize_gathered_facts",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "kt_worker_nodes.pipelines.gathering.pipeline.store_and_fetch",
            new_callable=AsyncMock,
            return_value=[mock_source],
        ),
        patch(
            "kt_worker_nodes.pipelines.gathering.pipeline.WritePageFetchLogRepository",
            return_value=mock_page_log,
        ),
        patch(
            "kt_worker_nodes.pipelines.gathering.pipeline.get_settings",
            return_value=mock_settings,
        ),
        patch(
            "kt_worker_nodes.pipelines.gathering.pipeline.DecompositionPipeline",
        ) as mock_decomp_cls,
    ):
        mock_decomp = MagicMock()
        mock_decomp.decompose = AsyncMock(return_value=mock_decomp_result)
        mock_decomp_cls.return_value = mock_decomp

        result = await pipeline.gather(
            ["query1"],
            state,
            enable_summary=False,
            enable_extraction=True,
        )

    assert "extracted_nodes" in result


@pytest.mark.asyncio
async def test_gather_default_mode_calls_summary_not_extraction() -> None:
    """Default mode: summary enabled, extraction not surfaced."""
    from kt_config.types import RawSearchResult

    ctx = MagicMock()
    ctx.provider_registry = MagicMock()
    search_results = {
        "query1": [RawSearchResult(title="r1", uri="http://r1.com", raw_content="c1", provider_id="test")]
    }
    ctx.provider_registry.search_all = AsyncMock(return_value=search_results)
    ctx.session = MagicMock()
    ctx.session.commit = AsyncMock()
    ctx.session.rollback = AsyncMock()
    ctx.graph_engine._write_session = AsyncMock()
    ctx.write_session_factory = None
    ctx.emit = AsyncMock()

    state = MagicMock()
    state.explore_remaining = 1
    state.explore_used = 0
    state.explore_budget = 1
    state.nav_budget = 0
    state.nav_used = 0
    state.gathered_fact_count = 0
    state.query = "test query"
    state.scope_description = "test scope"

    pipeline = GatherFactsPipeline(ctx)

    mock_decomp_result = MagicMock()
    mock_decomp_result.facts = [MagicMock()]
    mock_decomp_result.extracted_nodes = [{"name": "hidden", "node_type": "concept"}]
    mock_decomp_result.seed_keys = []

    mock_page_log = MagicMock()
    mock_page_log.check_urls_freshness = AsyncMock(return_value={})
    mock_page_log.record_fetch = AsyncMock()

    mock_settings = MagicMock()
    mock_settings.full_text_fetch_per_budget_point = 10
    mock_settings.fetch_guarantee_max_rounds = 1
    mock_settings.page_stale_days = 30

    mock_source = MagicMock()
    mock_source.is_super_source = False
    mock_source.is_full_text = True
    mock_source.uri = "http://r1.com"
    mock_source.id = "src-1"
    mock_source.raw_content = "c1"
    mock_source.provider_metadata = None
    mock_source.content_type = None

    with (
        patch(
            "kt_worker_nodes.pipelines.gathering.pipeline._summarize_gathered_facts",
            new_callable=AsyncMock,
            return_value={"content_summary": "test"},
        ) as mock_summarize,
        patch(
            "kt_worker_nodes.pipelines.gathering.pipeline.store_and_fetch",
            new_callable=AsyncMock,
            return_value=[mock_source],
        ),
        patch(
            "kt_worker_nodes.pipelines.gathering.pipeline.WritePageFetchLogRepository",
            return_value=mock_page_log,
        ),
        patch(
            "kt_worker_nodes.pipelines.gathering.pipeline.get_settings",
            return_value=mock_settings,
        ),
        patch(
            "kt_worker_nodes.pipelines.gathering.pipeline.DecompositionPipeline",
        ) as mock_decomp_cls,
    ):
        mock_decomp = MagicMock()
        mock_decomp.decompose = AsyncMock(return_value=mock_decomp_result)
        mock_decomp_cls.return_value = mock_decomp

        result = await pipeline.gather(["query1"], state)

    # summary was called, extraction NOT surfaced in result
    mock_summarize.assert_called_once()
    assert "content_summary" in result
    assert "extracted_nodes" not in result
