"""Tests for seed extraction from entity extraction output."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from seed_fixtures import (
    make_embedding_service_mock,
    make_fact_stub,
    make_qdrant_seed_repo_mock,
    make_seed_repo_mock,
)

from kt_db.keys import make_seed_key
from kt_facts.processing.seed_extraction import store_seeds_from_extracted_nodes


@pytest.mark.asyncio
class TestStoreSeedsFromExtractedNodes:
    async def test_empty_input(self) -> None:
        repo = make_seed_repo_mock()
        count, seed_keys = await store_seeds_from_extracted_nodes(
            [],
            [],
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        assert count == 0
        assert seed_keys == []

    async def test_no_extracted_nodes(self) -> None:
        repo = make_seed_repo_mock()
        facts = [make_fact_stub()]
        count, seed_keys = await store_seeds_from_extracted_nodes(
            [],
            facts,
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        assert count == 0
        assert seed_keys == []

    async def test_single_node_single_fact(self) -> None:
        repo = make_seed_repo_mock()
        repo.link_facts_batch = AsyncMock(return_value=1)
        facts = [make_fact_stub("Einstein developed relativity")]
        extracted = [
            {"name": "Albert Einstein", "node_type": "entity", "entity_subtype": "person", "fact_indices": [1]},
        ]
        count, seed_keys = await store_seeds_from_extracted_nodes(
            extracted,
            facts,
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        assert count == 1
        assert len(seed_keys) == 1
        expected_key = make_seed_key("Albert Einstein")
        # New API: batch upsert with aliases (status=pending)
        repo.upsert_seeds_batch_with_aliases.assert_called_once()
        batch_arg = repo.upsert_seeds_batch_with_aliases.call_args[0][0]
        assert len(batch_arg) == 1
        assert batch_arg[0]["key"] == expected_key
        assert batch_arg[0]["name"] == "Albert Einstein"
        repo.link_facts_batch.assert_called_once()

    async def test_two_nodes_same_fact_creates_edge_candidate(self) -> None:
        repo = make_seed_repo_mock()
        repo.link_facts_batch = AsyncMock(return_value=2)
        facts = [make_fact_stub("Einstein worked at Princeton")]
        extracted = [
            {"name": "Albert Einstein", "node_type": "entity", "entity_subtype": "person", "fact_indices": [1]},
            {
                "name": "Princeton University",
                "node_type": "entity",
                "entity_subtype": "organization",
                "fact_indices": [1],
            },
        ]
        count, seed_keys = await store_seeds_from_extracted_nodes(
            extracted,
            facts,
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        assert count == 2
        assert len(seed_keys) == 2
        # Should create edge candidates via batch method
        repo.upsert_edge_candidates_batch.assert_called_once()
        candidates = repo.upsert_edge_candidates_batch.call_args[0][0]
        assert len(candidates) == 1
        # Should be canonically ordered
        assert candidates[0]["seed_key_a"] < candidates[0]["seed_key_b"]
        assert candidates[0]["fact_id"] == str(facts[0].id)

    async def test_three_nodes_same_fact_creates_three_edge_candidates(self) -> None:
        repo = make_seed_repo_mock()
        repo.link_facts_batch = AsyncMock(return_value=3)
        facts = [make_fact_stub("Einstein at Princeton studied quantum mechanics")]
        extracted = [
            {"name": "Albert Einstein", "node_type": "entity", "fact_indices": [1]},
            {"name": "Princeton", "node_type": "entity", "fact_indices": [1]},
            {"name": "quantum mechanics", "node_type": "concept", "fact_indices": [1]},
        ]
        await store_seeds_from_extracted_nodes(
            extracted,
            facts,
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        # 3 choose 2 = 3 edge candidates via batch
        repo.upsert_edge_candidates_batch.assert_called_once()
        candidates = repo.upsert_edge_candidates_batch.call_args[0][0]
        assert len(candidates) == 3

    async def test_out_of_range_fact_indices_skipped(self) -> None:
        repo = make_seed_repo_mock()
        repo.link_facts_batch = AsyncMock(return_value=1)
        facts = [make_fact_stub()]
        extracted = [
            {"name": "Test", "node_type": "concept", "fact_indices": [0, 1, 2, -1]},
        ]
        count, seed_keys = await store_seeds_from_extracted_nodes(
            extracted,
            facts,
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        # Only fact_index=1 is valid (1-indexed, len=1)
        assert count == 1
        assert len(seed_keys) == 1

    async def test_multiple_facts_multiple_nodes(self) -> None:
        repo = make_seed_repo_mock()
        repo.link_facts_batch = AsyncMock(return_value=4)
        facts = [make_fact_stub("fact about Einstein"), make_fact_stub("fact about Bohr")]
        extracted = [
            {"name": "Einstein", "node_type": "entity", "fact_indices": [1]},
            {"name": "MIT", "node_type": "entity", "fact_indices": [1, 2]},
            {"name": "Bohr", "node_type": "entity", "fact_indices": [2]},
        ]
        count, seed_keys = await store_seeds_from_extracted_nodes(
            extracted,
            facts,
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        assert count == 4  # Einstein-1, MIT-1, MIT-2, Bohr-2
        assert len(seed_keys) == 3  # Einstein, MIT, Bohr

    async def test_no_name_skipped(self) -> None:
        repo = make_seed_repo_mock()
        repo.link_facts_batch = AsyncMock(return_value=1)
        facts = [make_fact_stub()]
        extracted = [
            {"node_type": "concept", "fact_indices": [1]},  # missing name
            {"name": "", "node_type": "concept", "fact_indices": [1]},  # empty name
            {"name": "Valid", "node_type": "concept", "fact_indices": [1]},
        ]
        count, seed_keys = await store_seeds_from_extracted_nodes(
            extracted,
            facts,
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        assert count == 1
        assert len(seed_keys) == 1

    async def test_aliases_stored_in_upsert(self) -> None:
        """LLM-provided aliases passed to upsert_seeds_batch_with_aliases as slugified keys."""
        repo = make_seed_repo_mock()
        repo.link_facts_batch = AsyncMock(return_value=1)
        facts = [make_fact_stub("The FBI investigates federal crimes")]
        extracted = [
            {
                "name": "Federal Bureau of Investigation",
                "node_type": "entity",
                "entity_subtype": "organization",
                "fact_indices": [1],
                "aliases": ["FBI"],
            },
        ]
        await store_seeds_from_extracted_nodes(
            extracted,
            facts,
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        # Aliases passed as slugified keys inside the batch upsert
        repo.upsert_seeds_batch_with_aliases.assert_called_once()
        batch_arg = repo.upsert_seeds_batch_with_aliases.call_args[0][0]
        assert len(batch_arg) == 1
        assert "fbi" in batch_arg[0]["aliases"]

    async def test_concept_without_entity_subtype(self) -> None:
        repo = make_seed_repo_mock()
        repo.link_facts_batch = AsyncMock(return_value=1)
        facts = [make_fact_stub()]
        extracted = [
            {"name": "quantum mechanics", "node_type": "concept", "fact_indices": [1]},
        ]
        await store_seeds_from_extracted_nodes(
            extracted,
            facts,
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        expected_key = make_seed_key("quantum mechanics")
        repo.upsert_seeds_batch_with_aliases.assert_called_once()
        batch_arg = repo.upsert_seeds_batch_with_aliases.call_args[0][0]
        assert len(batch_arg) == 1
        assert batch_arg[0]["key"] == expected_key
        assert batch_arg[0]["node_type"] == "concept"
        assert batch_arg[0]["entity_subtype"] is None

    async def test_alias_dsu_folds_matching_seeds(self) -> None:
        """If alias key of A matches canonical key of B, they merge in-memory (DSU)."""
        repo = make_seed_repo_mock()
        repo.link_facts_batch = AsyncMock(return_value=2)
        facts = [make_fact_stub("fact1"), make_fact_stub("fact2")]
        extracted = [
            # B's canonical key = make_seed_key("FBI")
            {"name": "FBI", "node_type": "entity", "fact_indices": [1]},
            # A has alias "FBI" → same key as B → should fold into one seed
            {
                "name": "Federal Bureau of Investigation",
                "node_type": "entity",
                "fact_indices": [2],
                "aliases": ["FBI"],
            },
        ]
        count, seed_keys = await store_seeds_from_extracted_nodes(
            extracted,
            facts,
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        # DSU should have merged into 1 representative
        assert len(seed_keys) == 1
        batch_arg = repo.upsert_seeds_batch_with_aliases.call_args[0][0]
        assert len(batch_arg) == 1
        # Longer name wins canonical
        assert batch_arg[0]["name"] == "Federal Bureau of Investigation"
        # Shorter key becomes alias
        assert "fbi" in batch_arg[0]["aliases"]
