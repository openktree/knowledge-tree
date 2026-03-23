"""Tests for seed disambiguation."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from kt_facts.processing.seed_disambiguation import (
    _execute_split,
    _heuristic_cluster,
    _llm_disambiguate,
    check_disambiguation,
)


def _make_seed(key, name, node_type, status="active", fact_count=15):
    seed = MagicMock()
    seed.key = key
    seed.name = name
    seed.node_type = node_type
    seed.status = status
    seed.fact_count = fact_count
    return seed


def _make_repo(seed=None, fact_ids=None):
    repo = MagicMock()
    repo.get_seed_by_key = AsyncMock(return_value=seed)
    repo.get_facts_for_seed = AsyncMock(return_value=fact_ids or [])
    repo.split_seed = AsyncMock()
    return repo


def _make_facts(n=10):
    return [{"id": uuid.uuid4(), "content": f"Fact {i} about the topic"} for i in range(n)]


def _make_write_fact_repo(facts):
    repo = MagicMock()
    loaded = []
    for f in facts:
        mock_fact = MagicMock()
        mock_fact.id = f["id"]
        mock_fact.content = f["content"]
        loaded.append(mock_fact)
    repo.get_by_ids = AsyncMock(return_value=loaded)
    return repo


@pytest.mark.asyncio
class TestCheckDisambiguation:
    async def test_below_threshold_returns_none(self):
        seed = _make_seed("entity:mars", "Mars", "entity", fact_count=3)
        repo = _make_repo(seed=seed)
        result = await check_disambiguation("entity:mars", repo)
        assert result is None

    async def test_inactive_seed_returns_none(self):
        seed = _make_seed("entity:mars", "Mars", "entity", status="merged", fact_count=15)
        repo = _make_repo(seed=seed)
        result = await check_disambiguation("entity:mars", repo)
        assert result is None

    async def test_missing_seed_returns_none(self):
        repo = _make_repo(seed=None)
        result = await check_disambiguation("entity:nonexistent", repo)
        assert result is None

    async def test_insufficient_facts_returns_none(self):
        seed = _make_seed("entity:mars", "Mars", "entity", fact_count=15)
        repo = _make_repo(seed=seed, fact_ids=[uuid.uuid4() for _ in range(3)])
        write_fact_repo = MagicMock()
        write_fact_repo.get_by_ids = AsyncMock(return_value=[])
        result = await check_disambiguation("entity:mars", repo, write_fact_repo=write_fact_repo)
        assert result is None


@pytest.mark.asyncio
class TestHeuristicCluster:
    async def test_single_cluster_returns_none(self):
        """All similar facts should produce one cluster."""
        facts = _make_facts(5)
        embedding_service = MagicMock()
        # All embeddings are very similar
        embedding_service.embed_batch = AsyncMock(return_value=[[0.1] * 10 for _ in range(5)])

        result = await _heuristic_cluster(facts, embedding_service, threshold=0.85)
        assert result is None

    async def test_too_few_facts_returns_none(self):
        facts = _make_facts(2)
        embedding_service = MagicMock()
        result = await _heuristic_cluster(facts, embedding_service, threshold=0.85)
        assert result is None

    async def test_embedding_failure_returns_none(self):
        facts = _make_facts(5)
        embedding_service = MagicMock()
        embedding_service.embed_batch = AsyncMock(side_effect=Exception("API error"))
        result = await _heuristic_cluster(facts, embedding_service, threshold=0.85)
        assert result is None

    async def test_distinct_clusters_detected(self):
        """Two clearly distinct groups of facts should be split."""
        facts = _make_facts(6)
        embedding_service = MagicMock()
        # Create two clearly distinct embedding clusters
        cluster1 = [[1.0, 0.0, 0.0] + [0.0] * 7 for _ in range(3)]
        cluster2 = [[0.0, 0.0, 1.0] + [0.0] * 7 for _ in range(3)]
        embedding_service.embed_batch = AsyncMock(return_value=cluster1 + cluster2)

        result = await _heuristic_cluster(facts, embedding_service, threshold=0.85)
        assert result is not None
        assert len(result) == 2


@pytest.mark.asyncio
class TestLlmDisambiguate:
    async def test_not_ambiguous(self):
        facts = _make_facts(5)
        gateway = MagicMock()
        gateway.default_model = "test-model"
        gateway.generate_json = AsyncMock(return_value={
            "is_ambiguous": False,
            "groups": [{"label": "Mars (planet)", "fact_numbers": [1, 2, 3, 4, 5]}],
        })

        result = await _llm_disambiguate("Mars", "entity", facts, gateway)
        assert result is None

    async def test_ambiguous_split(self):
        facts = _make_facts(6)
        gateway = MagicMock()
        gateway.default_model = "test-model"
        gateway.generate_json = AsyncMock(return_value={
            "is_ambiguous": True,
            "groups": [
                {"label": "Mars (planet)", "fact_numbers": [1, 2, 3]},
                {"label": "Mars (Roman god)", "fact_numbers": [4, 5, 6]},
            ],
        })

        result = await _llm_disambiguate("Mars", "entity", facts, gateway)
        assert result is not None
        assert isinstance(result, dict)
        clusters = result["clusters"]
        labels = result["labels"]
        assert len(clusters) == 2
        assert len(clusters[0]) == 3
        assert len(clusters[1]) == 3
        assert labels[0] == "Mars (planet)"
        assert labels[1] == "Mars (Roman god)"

    async def test_llm_error_returns_none(self):
        facts = _make_facts(5)
        gateway = MagicMock()
        gateway.default_model = "test-model"
        gateway.generate_json = AsyncMock(side_effect=Exception("LLM error"))

        result = await _llm_disambiguate("Mars", "entity", facts, gateway)
        assert result is None


@pytest.mark.asyncio
class TestExecuteSplit:
    async def test_split_creates_new_seeds(self):
        repo = _make_repo()
        clusters = [
            [{"id": uuid.uuid4(), "content": "Fact 1"}, {"id": uuid.uuid4(), "content": "Fact 2"}],
            [{"id": uuid.uuid4(), "content": "Fact 3"}, {"id": uuid.uuid4(), "content": "Fact 4"}],
        ]

        result = await _execute_split(
            "entity:mars", "Mars", "entity", clusters, repo, "test reason",
        )

        assert len(result) == 2
        assert all("key" in s and "name" in s and "node_type" in s for s in result)
        repo.split_seed.assert_called_once()
        call_kwargs = repo.split_seed.call_args
        assert call_kwargs[1]["original_key"] == "entity:mars"
        assert call_kwargs[1]["reason"] == "test reason"
        assert len(call_kwargs[1]["new_seeds"]) == 2
