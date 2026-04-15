"""Shared mock factories for seed dedup/routing/extraction tests.

Centralises the stubs that were duplicated across test_seed_dedup.py,
test_seed_routing.py, and test_seed_extraction.py.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

from kt_facts.processing.seed_heuristics import QdrantMatchStub, RouteStub, SeedStub


def make_seed(
    key: str,
    name: str,
    node_type: str,
    status: str = "active",
    fact_count: int = 1,
    merged_into_key: str | None = None,
    metadata_: dict | None = None,
    aliases: list[str] | None = None,
) -> SeedStub:
    """Build a SeedStub with sensible defaults."""
    return SeedStub(
        key=key,
        name=name,
        node_type=node_type,
        status=status,
        fact_count=fact_count,
        merged_into_key=merged_into_key,
        metadata_=metadata_,
        context_hash=None,
        aliases=aliases or [],
    )


def make_route(parent_key: str, child_key: str, label: str, ambiguity_type: str = "text") -> RouteStub:
    """Build a RouteStub."""
    return RouteStub(
        parent_seed_key=parent_key,
        child_seed_key=child_key,
        label=label,
        ambiguity_type=ambiguity_type,
    )


def make_qdrant_match(seed_key: str, score: float) -> QdrantMatchStub:
    """Build a QdrantMatchStub."""
    return QdrantMatchStub(seed_key=seed_key, score=score)


def make_seed_repo_mock(**overrides: object) -> MagicMock:
    """Standard WriteSeedRepository mock with all methods pre-configured."""
    repo = MagicMock()
    # New pipeline methods
    repo.find_seeds_by_keys_or_aliases = AsyncMock(return_value=[])
    repo.set_status = AsyncMock()
    repo.merge_aliases_into_winner = AsyncMock()
    repo.rename_seed = AsyncMock()
    repo.get_facts_with_content_for_seed = AsyncMock(return_value=[])
    repo.upsert_seeds_batch_with_aliases = AsyncMock()
    repo.upsert_seed_with_aliases = AsyncMock()
    # Shared methods
    repo.get_seed_by_key = AsyncMock(return_value=None)
    repo.merge_seeds = AsyncMock()
    repo.create_route = AsyncMock()
    repo.get_routes_for_parent = AsyncMock(return_value=[])
    repo.get_facts_for_seed = AsyncMock(return_value=[])
    repo.get_seeds_by_keys_batch = AsyncMock(return_value={})
    repo.split_seed = AsyncMock()
    repo.link_fact = AsyncMock(return_value=True)
    repo.link_facts_batch = AsyncMock(return_value=0)
    repo.refresh_fact_counts = AsyncMock()
    repo.upsert_edge_candidate = AsyncMock()
    repo.upsert_edge_candidates_batch = AsyncMock()
    repo._session = MagicMock()
    repo._session.execute = AsyncMock()
    repo._session.begin_nested = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(), __aexit__=AsyncMock())
    )
    for key, val in overrides.items():
        setattr(repo, key, val)
    return repo


def make_embedding_service_mock(embedding: list[float] | None = None) -> MagicMock:
    """Standard EmbeddingService mock."""
    svc = MagicMock()
    svc.embed_text = AsyncMock(return_value=embedding or [0.1] * 10)
    return svc


def make_qdrant_seed_repo_mock(hits: list | None = None) -> MagicMock:
    """Standard QdrantSeedRepository mock."""
    repo = MagicMock()
    repo.upsert = AsyncMock()
    repo.find_similar = AsyncMock(return_value=hits or [])
    return repo


def make_model_gateway_mock(result: dict | None = None) -> MagicMock:
    """Standard ModelGateway mock."""
    gw = MagicMock()
    gw.default_model = "test-model"
    gw.generate_json = AsyncMock(return_value=result or {})
    return gw


def make_write_fact_repo_mock(facts: list | None = None) -> MagicMock:
    """Standard WriteFactRepository mock."""
    repo = MagicMock()
    repo.get_by_ids = AsyncMock(return_value=facts or [])
    return repo


def make_fact_stub(content: str = "test fact") -> MagicMock:
    """Build a mock fact with id and content."""
    f = MagicMock()
    f.id = uuid.uuid4()
    f.content = content
    f.fact_type = "claim"
    return f
