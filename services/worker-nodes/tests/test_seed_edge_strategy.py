"""Tests for seed-candidate-based edge resolution (EdgeResolver.resolve_from_candidates)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kt_db.keys import key_to_uuid, make_seed_key


def _make_ctx(write_session=None):
    """Build a minimal AgentContext mock."""
    ctx = MagicMock()
    ctx.graph_engine = MagicMock()
    ctx.graph_engine._write_session = write_session
    ctx.graph_engine.create_edge = AsyncMock(return_value=MagicMock(id=uuid.uuid4()))
    ctx.graph_engine.get_facts_by_ids = AsyncMock(return_value=[])
    ctx.model_gateway = MagicMock()
    ctx.model_gateway.generate_json = AsyncMock(return_value=[{"justification": "test"}])
    ctx.emit = AsyncMock()
    return ctx


def _make_node(concept="test concept", node_type="concept", node_id=None):
    node = MagicMock()
    node.id = node_id or key_to_uuid(make_seed_key(node_type, concept))
    node.concept = concept
    node.node_type = node_type
    return node


def _make_seed(key, name, node_type, status="active", promoted_node_key=None):
    seed = MagicMock()
    seed.key = key
    seed.name = name
    seed.node_type = node_type
    seed.status = status
    seed.promoted_node_key = promoted_node_key
    return seed


def _make_candidate_row(seed_key_a, seed_key_b, fact_id):
    """Create a mock WriteEdgeCandidate row."""
    row = MagicMock()
    # Ensure canonical ordering
    a, b = sorted([seed_key_a, seed_key_b])
    row.seed_key_a = a
    row.seed_key_b = b
    row.fact_id = str(fact_id)
    row.status = "pending"
    return row


@pytest.mark.asyncio
class TestResolveFromCandidates:
    async def test_no_write_session_returns_empty(self):
        from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

        ctx = _make_ctx(write_session=None)
        node = _make_node()
        result = await EdgeResolver(ctx).resolve_from_candidates(node)
        assert result == {"edges_created": 0, "edge_ids": []}

    async def test_no_candidates_returns_empty(self):
        from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

        write_session = MagicMock()
        ctx = _make_ctx(write_session=write_session)
        node = _make_node(concept="unknown concept")

        with patch("kt_worker_nodes.pipelines.edges.resolver.WriteSeedRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_candidates_for_seed = AsyncMock(return_value=[])

            result = await EdgeResolver(ctx).resolve_from_candidates(node)

        assert result == {"edges_created": 0, "edge_ids": []}

    async def test_promoted_seed_produces_edge(self):
        from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

        write_session = MagicMock()
        ctx = _make_ctx(write_session=write_session)
        node = _make_node(concept="quantum mechanics", node_type="concept")

        source_key = make_seed_key("concept", "quantum mechanics")
        target_key = make_seed_key("entity", "Niels Bohr")
        fids = [uuid.uuid4() for _ in range(3)]

        target_seed = _make_seed(
            target_key,
            "Niels Bohr",
            "entity",
            status="promoted",
            promoted_node_key=target_key,
        )

        rows = [_make_candidate_row(source_key, target_key, fid) for fid in fids]

        mock_facts = [
            MagicMock(id=fid, content=f"Bohr contributed to QM {i}", fact_type="claim") for i, fid in enumerate(fids)
        ]
        ctx.graph_engine.get_facts_by_ids = AsyncMock(return_value=mock_facts)

        with patch("kt_worker_nodes.pipelines.edges.resolver.WriteSeedRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_candidates_for_seed = AsyncMock(return_value=rows)
            mock_repo.get_seed_by_key = AsyncMock(return_value=target_seed)
            mock_repo.accept_candidate_facts = AsyncMock()

            result = await EdgeResolver(ctx).resolve_from_candidates(node)

        assert result["edges_created"] == 1
        assert len(result["edge_ids"]) == 1
        # Cross-type since entity != concept
        call_args = ctx.graph_engine.create_edge.call_args
        assert call_args[0][2] == "cross_type"

    async def test_unpromoted_seed_filtered_out(self):
        from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

        write_session = MagicMock()
        ctx = _make_ctx(write_session=write_session)
        node = _make_node(concept="physics", node_type="concept")

        source_key = make_seed_key("concept", "physics")
        other_key = make_seed_key("concept", "thermodynamics")

        other_seed = _make_seed(other_key, "thermodynamics", "concept", status="active")  # not promoted

        row = _make_candidate_row(source_key, other_key, uuid.uuid4())

        with patch("kt_worker_nodes.pipelines.edges.resolver.WriteSeedRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_candidates_for_seed = AsyncMock(return_value=[row])
            mock_repo.get_seed_by_key = AsyncMock(return_value=other_seed)

            result = await EdgeResolver(ctx).resolve_from_candidates(node)

        assert result["edges_created"] == 0

    async def test_same_type_uses_related_edge_type(self):
        from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

        write_session = MagicMock()
        ctx = _make_ctx(write_session=write_session)
        node = _make_node(concept="biology", node_type="concept")

        source_key = make_seed_key("concept", "biology")
        target_key = make_seed_key("concept", "genetics")
        fids = [uuid.uuid4() for _ in range(3)]

        target_seed = _make_seed(
            target_key,
            "genetics",
            "concept",
            status="promoted",
            promoted_node_key=target_key,
        )

        rows = [_make_candidate_row(source_key, target_key, fid) for fid in fids]

        mock_facts = [
            MagicMock(id=fid, content=f"genetics is a branch of biology {i}", fact_type="claim")
            for i, fid in enumerate(fids)
        ]
        ctx.graph_engine.get_facts_by_ids = AsyncMock(return_value=mock_facts)

        with patch("kt_worker_nodes.pipelines.edges.resolver.WriteSeedRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_candidates_for_seed = AsyncMock(return_value=rows)
            mock_repo.get_seed_by_key = AsyncMock(return_value=target_seed)
            mock_repo.accept_candidate_facts = AsyncMock()

            result = await EdgeResolver(ctx).resolve_from_candidates(node)

        assert result["edges_created"] == 1
        call_args = ctx.graph_engine.create_edge.call_args
        assert call_args[0][2] == "related"

    async def test_accepts_candidate_facts_after_edge_creation(self):
        from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

        write_session = MagicMock()
        ctx = _make_ctx(write_session=write_session)
        node = _make_node(concept="math", node_type="concept")

        source_key = make_seed_key("concept", "math")
        target_key = make_seed_key("concept", "algebra")
        fids = [uuid.uuid4() for _ in range(3)]

        target_seed = _make_seed(
            target_key,
            "algebra",
            "concept",
            status="promoted",
            promoted_node_key=target_key,
        )

        rows = [_make_candidate_row(source_key, target_key, fid) for fid in fids]

        mock_facts = [
            MagicMock(id=fid, content=f"algebra is a branch of math {i}", fact_type="claim")
            for i, fid in enumerate(fids)
        ]
        ctx.graph_engine.get_facts_by_ids = AsyncMock(return_value=mock_facts)

        with patch("kt_worker_nodes.pipelines.edges.resolver.WriteSeedRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_candidates_for_seed = AsyncMock(return_value=rows)
            mock_repo.get_seed_by_key = AsyncMock(return_value=target_seed)
            mock_repo.accept_candidate_facts = AsyncMock()

            await EdgeResolver(ctx).resolve_from_candidates(node)

            # Verify accept_candidate_facts was called
            mock_repo.accept_candidate_facts.assert_called_once()

    async def test_missing_seed_skipped(self):
        from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

        write_session = MagicMock()
        ctx = _make_ctx(write_session=write_session)
        node = _make_node(concept="chemistry", node_type="concept")

        source_key = make_seed_key("concept", "chemistry")
        other_key = make_seed_key("concept", "organic chemistry")

        row = _make_candidate_row(source_key, other_key, uuid.uuid4())

        with patch("kt_worker_nodes.pipelines.edges.resolver.WriteSeedRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_candidates_for_seed = AsyncMock(return_value=[row])
            # get_seed_by_key returns None — seed doesn't exist
            mock_repo.get_seed_by_key = AsyncMock(return_value=None)

            result = await EdgeResolver(ctx).resolve_from_candidates(node)

        assert result["edges_created"] == 0

    async def test_weight_scales_with_fact_count(self):
        from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

        write_session = MagicMock()
        ctx = _make_ctx(write_session=write_session)
        node = _make_node(concept="evolution", node_type="concept")

        source_key = make_seed_key("concept", "evolution")
        target_key = make_seed_key("concept", "natural selection")
        fact_ids = [uuid.uuid4() for _ in range(5)]

        target_seed = _make_seed(
            target_key,
            "natural selection",
            "concept",
            status="promoted",
            promoted_node_key=target_key,
        )

        rows = [_make_candidate_row(source_key, target_key, fid) for fid in fact_ids]

        facts = [MagicMock(id=fid, content=f"fact {i}", fact_type="claim") for i, fid in enumerate(fact_ids)]
        ctx.graph_engine.get_facts_by_ids = AsyncMock(return_value=facts)

        with patch("kt_worker_nodes.pipelines.edges.resolver.WriteSeedRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_candidates_for_seed = AsyncMock(return_value=rows)
            mock_repo.get_seed_by_key = AsyncMock(return_value=target_seed)
            mock_repo.accept_candidate_facts = AsyncMock()

            result = await EdgeResolver(ctx).resolve_from_candidates(node)

        assert result["edges_created"] == 1
        # Weight = raw fact count = 5.0
        call_args = ctx.graph_engine.create_edge.call_args
        weight = call_args[0][3]
        assert weight == 5.0
