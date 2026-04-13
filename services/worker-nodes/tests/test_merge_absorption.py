"""Tests for merge absorption in auto_build — absorbing loser nodes into winners."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_seed(
    key: str,
    name: str,
    node_type: str = "concept",
    status: str = "merged",
    promoted_node_key: str | None = None,
    merged_into_key: str | None = None,
    fact_count: int = 5,
) -> MagicMock:
    seed = MagicMock()
    seed.key = key
    seed.name = name
    seed.node_type = node_type
    seed.status = status
    seed.promoted_node_key = promoted_node_key
    seed.merged_into_key = merged_into_key
    seed.fact_count = fact_count
    return seed


def _make_write_node(
    key: str, concept: str, node_type: str = "concept", fact_ids: list[str] | None = None
) -> MagicMock:
    node = MagicMock()
    node.key = key
    node.concept = concept
    node.node_type = node_type
    node.node_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, key)
    node.fact_ids = fact_ids
    return node


def _make_dimension(
    key: str,
    node_key: str,
    model_id: str = "model-a",
    batch_index: int = 0,
) -> MagicMock:
    dim = MagicMock()
    dim.key = key
    dim.node_key = node_key
    dim.model_id = model_id
    dim.content = "test dimension content"
    dim.confidence = 0.8
    dim.suggested_concepts = None
    dim.batch_index = batch_index
    dim.fact_count = 3
    dim.is_definitive = False
    dim.fact_ids = ["fact-1", "fact-2"]
    dim.metadata_ = None
    return dim


def _make_edge(
    key: str,
    source_key: str,
    target_key: str,
    rel_type: str = "related",
    weight: float = 0.5,
    fact_ids: list[str] | None = None,
) -> MagicMock:
    edge = MagicMock()
    edge.key = key
    edge.source_node_key = source_key
    edge.target_node_key = target_key
    edge.relationship_type = rel_type
    edge.weight = weight
    edge.fact_ids = fact_ids or ["f1"]
    edge.justification = "test justification"
    edge.metadata_ = None
    edge.weight_source = "cooccurrence"
    return edge


def _make_state(write_sf=None, qdrant_client=None):
    state = MagicMock()
    state.write_session_factory = write_sf
    state.qdrant_client = qdrant_client
    return state


def _make_settings():
    settings = MagicMock()
    settings.graph_build_batch_size = 100
    return settings


def _make_ctx():
    ctx = MagicMock()
    ctx.aio_put_stream = AsyncMock()
    return ctx


class _FakeSessionContext:
    """Async context manager that yields a mock session."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *args):
        pass


# Patch paths target the source modules (lazy imports inside the function)
_SEED_REPO = "kt_db.repositories.write_seeds.WriteSeedRepository"
_NODE_REPO = "kt_db.repositories.write_nodes.WriteNodeRepository"
_EDGE_REPO = "kt_db.repositories.write_edges.WriteEdgeRepository"
_DIM_REPO = "kt_db.repositories.write_dimensions.WriteDimensionRepository"
_QDRANT_NODE_REPO = "kt_qdrant.repositories.nodes.QdrantNodeRepository"


@pytest.mark.asyncio
class TestAbsorbMergedNodes:
    async def test_basic_absorption(self):
        """Loser node's dimensions, edges, facts are transferred to winner."""
        from kt_worker_nodes.workflows.auto_build import _absorb_merged_nodes

        loser_seed = _make_seed(
            "old",
            "Old Name",
            promoted_node_key="old",
            merged_into_key="new",
        )
        winner_seed = _make_seed(
            "new",
            "New Name",
            status="promoted",
            promoted_node_key="new",
        )
        loser_node = _make_write_node("old", "Old Name", fact_ids=["f1", "f2"])
        winner_node = _make_write_node("new", "New Name", fact_ids=["f3"])
        dim = _make_dimension("old|model-a|0", "old")
        edge = _make_edge("related|old|concept:third", "old", "third")

        ws = MagicMock()
        ws.commit = AsyncMock()

        with (
            patch(_SEED_REPO) as MockSeedRepo,
            patch(_NODE_REPO) as MockNodeRepo,
            patch(_EDGE_REPO) as MockEdgeRepo,
            patch(_DIM_REPO) as MockDimRepo,
        ):
            seed_repo = MockSeedRepo.return_value
            seed_repo.get_merged_promoted_seeds = AsyncMock(return_value=[loser_seed])
            seed_repo.get_seed_by_key = AsyncMock(return_value=winner_seed)
            seed_repo.clear_promoted_node_key = AsyncMock()

            node_repo = MockNodeRepo.return_value
            node_repo.get_by_key = AsyncMock(
                side_effect=lambda k: {
                    "new": winner_node,
                    "old": loser_node,
                }.get(k)
            )
            node_repo.merge_fact_ids = AsyncMock()
            node_repo.delete_by_key = AsyncMock()

            dim_repo = MockDimRepo.return_value
            dim_repo.get_by_node_key = AsyncMock(return_value=[dim])
            dim_repo.upsert = AsyncMock()
            dim_repo.delete_by_key = AsyncMock()
            dim_repo.delete_convergence_report = AsyncMock()
            dim_repo.delete_divergent_claims = AsyncMock()

            edge_repo = MockEdgeRepo.return_value
            edge_repo.get_edges_for_node = AsyncMock(return_value=[edge])
            edge_repo.upsert = AsyncMock()
            edge_repo.delete_by_key = AsyncMock()

            state = _make_state(write_sf=lambda: _FakeSessionContext(ws))
            result = await _absorb_merged_nodes(state, _make_settings(), _make_ctx())

        assert result == 1

        # Dimension transferred to winner
        dim_repo.upsert.assert_called_once()
        call_kwargs = dim_repo.upsert.call_args
        assert call_kwargs.kwargs["node_key"] == "new"

        # Old dimension deleted
        dim_repo.delete_by_key.assert_called_once_with(dim.key)

        # Edge upserted with winner key
        edge_repo.upsert.assert_called_once()
        edge_call = edge_repo.upsert.call_args
        assert edge_call.kwargs["source_node_key"] == "new"

        # Facts merged
        node_repo.merge_fact_ids.assert_called_once_with("new", ["f1", "f2"])

        # Loser node deleted
        node_repo.delete_by_key.assert_called_once_with("old")

        # Seed cleared
        seed_repo.clear_promoted_node_key.assert_called_once_with("old")

    async def test_skips_when_winner_not_promoted(self):
        """If winner seed has no promoted_node_key, skip (retry next run)."""
        from kt_worker_nodes.workflows.auto_build import _absorb_merged_nodes

        loser_seed = _make_seed(
            "old",
            "Old",
            promoted_node_key="old",
            merged_into_key="new",
        )
        winner_seed = _make_seed(
            "new",
            "New",
            status="active",
            promoted_node_key=None,
        )

        ws = MagicMock()
        ws.commit = AsyncMock()

        with (
            patch(_SEED_REPO) as MockSeedRepo,
            patch(_NODE_REPO) as MockNodeRepo,
            patch(_EDGE_REPO),
            patch(_DIM_REPO),
        ):
            seed_repo = MockSeedRepo.return_value
            seed_repo.get_merged_promoted_seeds = AsyncMock(return_value=[loser_seed])
            seed_repo.get_seed_by_key = AsyncMock(return_value=winner_seed)
            seed_repo.clear_promoted_node_key = AsyncMock()

            node_repo = MockNodeRepo.return_value
            node_repo.get_by_key = AsyncMock()
            node_repo.delete_by_key = AsyncMock()

            state = _make_state(write_sf=lambda: _FakeSessionContext(ws))
            result = await _absorb_merged_nodes(state, _make_settings(), _make_ctx())

        assert result == 0
        node_repo.delete_by_key.assert_not_called()
        seed_repo.clear_promoted_node_key.assert_not_called()

    async def test_skips_when_loser_node_missing(self):
        """If loser node already deleted, clear seed and skip."""
        from kt_worker_nodes.workflows.auto_build import _absorb_merged_nodes

        loser_seed = _make_seed(
            "old",
            "Old",
            promoted_node_key="old",
            merged_into_key="new",
        )
        winner_seed = _make_seed(
            "new",
            "New",
            status="promoted",
            promoted_node_key="new",
        )
        winner_node = _make_write_node("new", "New")

        ws = MagicMock()
        ws.commit = AsyncMock()

        with (
            patch(_SEED_REPO) as MockSeedRepo,
            patch(_NODE_REPO) as MockNodeRepo,
            patch(_EDGE_REPO),
            patch(_DIM_REPO),
        ):
            seed_repo = MockSeedRepo.return_value
            seed_repo.get_merged_promoted_seeds = AsyncMock(return_value=[loser_seed])
            seed_repo.get_seed_by_key = AsyncMock(return_value=winner_seed)
            seed_repo.clear_promoted_node_key = AsyncMock()

            node_repo = MockNodeRepo.return_value
            node_repo.get_by_key = AsyncMock(
                side_effect=lambda k: {
                    "new": winner_node,
                }.get(k)
            )  # loser returns None

            state = _make_state(write_sf=lambda: _FakeSessionContext(ws))
            result = await _absorb_merged_nodes(state, _make_settings(), _make_ctx())

        assert result == 0
        # Still clears promoted_node_key since node is already gone
        seed_repo.clear_promoted_node_key.assert_called_once_with("old")

    async def test_self_edge_deleted(self):
        """Edge between loser and winner becomes self-edge — should be deleted."""
        from kt_worker_nodes.workflows.auto_build import _absorb_merged_nodes

        loser_seed = _make_seed(
            "old",
            "Old",
            promoted_node_key="old",
            merged_into_key="new",
        )
        winner_seed = _make_seed(
            "new",
            "New",
            status="promoted",
            promoted_node_key="new",
        )
        loser_node = _make_write_node("old", "Old", fact_ids=[])
        winner_node = _make_write_node("new", "New")

        # Edge from loser to winner — becomes self-edge after absorption
        self_edge = _make_edge(
            "related|new|concept:old",
            "new",
            "old",
        )

        ws = MagicMock()
        ws.commit = AsyncMock()

        with (
            patch(_SEED_REPO) as MockSeedRepo,
            patch(_NODE_REPO) as MockNodeRepo,
            patch(_EDGE_REPO) as MockEdgeRepo,
            patch(_DIM_REPO) as MockDimRepo,
        ):
            seed_repo = MockSeedRepo.return_value
            seed_repo.get_merged_promoted_seeds = AsyncMock(return_value=[loser_seed])
            seed_repo.get_seed_by_key = AsyncMock(return_value=winner_seed)
            seed_repo.clear_promoted_node_key = AsyncMock()

            node_repo = MockNodeRepo.return_value
            node_repo.get_by_key = AsyncMock(
                side_effect=lambda k: {
                    "new": winner_node,
                    "old": loser_node,
                }.get(k)
            )
            node_repo.merge_fact_ids = AsyncMock()
            node_repo.delete_by_key = AsyncMock()

            dim_repo = MockDimRepo.return_value
            dim_repo.get_by_node_key = AsyncMock(return_value=[])
            dim_repo.delete_convergence_report = AsyncMock()
            dim_repo.delete_divergent_claims = AsyncMock()

            edge_repo = MockEdgeRepo.return_value
            edge_repo.get_edges_for_node = AsyncMock(return_value=[self_edge])
            edge_repo.upsert = AsyncMock()
            edge_repo.delete_by_key = AsyncMock()

            state = _make_state(write_sf=lambda: _FakeSessionContext(ws))
            result = await _absorb_merged_nodes(state, _make_settings(), _make_ctx())

        assert result == 1
        # Self-edge deleted, not upserted
        edge_repo.upsert.assert_not_called()
        edge_repo.delete_by_key.assert_any_call(self_edge.key)

    async def test_qdrant_deletion(self):
        """Qdrant vector for loser node should be deleted."""
        from kt_worker_nodes.workflows.auto_build import _absorb_merged_nodes

        loser_seed = _make_seed(
            "old",
            "Old",
            promoted_node_key="old",
            merged_into_key="new",
        )
        winner_seed = _make_seed(
            "new",
            "New",
            status="promoted",
            promoted_node_key="new",
        )
        loser_node = _make_write_node("old", "Old", fact_ids=[])
        winner_node = _make_write_node("new", "New")

        ws = MagicMock()
        ws.commit = AsyncMock()
        qdrant = MagicMock()

        with (
            patch(_SEED_REPO) as MockSeedRepo,
            patch(_NODE_REPO) as MockNodeRepo,
            patch(_EDGE_REPO) as MockEdgeRepo,
            patch(_DIM_REPO) as MockDimRepo,
            patch(_QDRANT_NODE_REPO) as MockQdrantRepo,
        ):
            seed_repo = MockSeedRepo.return_value
            seed_repo.get_merged_promoted_seeds = AsyncMock(return_value=[loser_seed])
            seed_repo.get_seed_by_key = AsyncMock(return_value=winner_seed)
            seed_repo.clear_promoted_node_key = AsyncMock()

            node_repo = MockNodeRepo.return_value
            node_repo.get_by_key = AsyncMock(
                side_effect=lambda k: {
                    "new": winner_node,
                    "old": loser_node,
                }.get(k)
            )
            node_repo.merge_fact_ids = AsyncMock()
            node_repo.delete_by_key = AsyncMock()

            dim_repo = MockDimRepo.return_value
            dim_repo.get_by_node_key = AsyncMock(return_value=[])
            dim_repo.delete_convergence_report = AsyncMock()
            dim_repo.delete_divergent_claims = AsyncMock()

            edge_repo = MockEdgeRepo.return_value
            edge_repo.get_edges_for_node = AsyncMock(return_value=[])

            qdrant_node_repo = MockQdrantRepo.return_value
            qdrant_node_repo.delete = AsyncMock()

            state = _make_state(
                write_sf=lambda: _FakeSessionContext(ws),
                qdrant_client=qdrant,
            )
            result = await _absorb_merged_nodes(state, _make_settings(), _make_ctx())

        assert result == 1
        qdrant_node_repo.delete.assert_called_once()

    async def test_no_write_session_returns_zero(self):
        """No write session factory — returns 0 without error."""
        from kt_worker_nodes.workflows.auto_build import _absorb_merged_nodes

        state = _make_state(write_sf=None)
        result = await _absorb_merged_nodes(state, _make_settings(), _make_ctx())
        assert result == 0

    async def test_dimensions_rekeyed_to_winner(self):
        """Dimensions from loser should be upserted under winner node_key."""
        from kt_worker_nodes.workflows.auto_build import _absorb_merged_nodes

        loser_seed = _make_seed(
            "old",
            "Old",
            promoted_node_key="old",
            merged_into_key="new",
        )
        winner_seed = _make_seed(
            "new",
            "New",
            status="promoted",
            promoted_node_key="new",
        )
        loser_node = _make_write_node("old", "Old", fact_ids=[])
        winner_node = _make_write_node("new", "New")

        dim_a = _make_dimension("old|model-a|0", "old", "model-a", 0)
        dim_b = _make_dimension("old|model-b|0", "old", "model-b", 0)

        ws = MagicMock()
        ws.commit = AsyncMock()

        with (
            patch(_SEED_REPO) as MockSeedRepo,
            patch(_NODE_REPO) as MockNodeRepo,
            patch(_EDGE_REPO) as MockEdgeRepo,
            patch(_DIM_REPO) as MockDimRepo,
        ):
            seed_repo = MockSeedRepo.return_value
            seed_repo.get_merged_promoted_seeds = AsyncMock(return_value=[loser_seed])
            seed_repo.get_seed_by_key = AsyncMock(return_value=winner_seed)
            seed_repo.clear_promoted_node_key = AsyncMock()

            node_repo = MockNodeRepo.return_value
            node_repo.get_by_key = AsyncMock(
                side_effect=lambda k: {
                    "new": winner_node,
                    "old": loser_node,
                }.get(k)
            )
            node_repo.merge_fact_ids = AsyncMock()
            node_repo.delete_by_key = AsyncMock()

            dim_repo = MockDimRepo.return_value
            dim_repo.get_by_node_key = AsyncMock(return_value=[dim_a, dim_b])
            dim_repo.upsert = AsyncMock()
            dim_repo.delete_by_key = AsyncMock()
            dim_repo.delete_convergence_report = AsyncMock()
            dim_repo.delete_divergent_claims = AsyncMock()

            edge_repo = MockEdgeRepo.return_value
            edge_repo.get_edges_for_node = AsyncMock(return_value=[])

            state = _make_state(write_sf=lambda: _FakeSessionContext(ws))
            result = await _absorb_merged_nodes(state, _make_settings(), _make_ctx())

        assert result == 1
        assert dim_repo.upsert.call_count == 2
        # Both dimensions rekeyed to winner
        for call in dim_repo.upsert.call_args_list:
            assert call.kwargs["node_key"] == "new"
        # Old dimensions deleted
        assert dim_repo.delete_by_key.call_count == 2

    async def test_edge_rekeyed_to_winner(self):
        """Edge from loser should be deleted and upserted under winner node key."""
        from kt_worker_nodes.workflows.auto_build import _absorb_merged_nodes

        loser_seed = _make_seed(
            "old",
            "Old",
            promoted_node_key="old",
            merged_into_key="new",
        )
        winner_seed = _make_seed(
            "new",
            "New",
            status="promoted",
            promoted_node_key="new",
        )
        loser_node = _make_write_node("old", "Old", fact_ids=[])
        winner_node = _make_write_node("new", "New")

        edge = _make_edge(
            "related|old|concept:third",
            "old",
            "third",
        )

        ws = MagicMock()
        ws.commit = AsyncMock()

        with (
            patch(_SEED_REPO) as MockSeedRepo,
            patch(_NODE_REPO) as MockNodeRepo,
            patch(_EDGE_REPO) as MockEdgeRepo,
            patch(_DIM_REPO) as MockDimRepo,
        ):
            seed_repo = MockSeedRepo.return_value
            seed_repo.get_merged_promoted_seeds = AsyncMock(return_value=[loser_seed])
            seed_repo.get_seed_by_key = AsyncMock(return_value=winner_seed)
            seed_repo.clear_promoted_node_key = AsyncMock()

            node_repo = MockNodeRepo.return_value
            node_repo.get_by_key = AsyncMock(
                side_effect=lambda k: {
                    "new": winner_node,
                    "old": loser_node,
                }.get(k)
            )
            node_repo.merge_fact_ids = AsyncMock()
            node_repo.delete_by_key = AsyncMock()

            dim_repo = MockDimRepo.return_value
            dim_repo.get_by_node_key = AsyncMock(return_value=[])
            dim_repo.delete_convergence_report = AsyncMock()
            dim_repo.delete_divergent_claims = AsyncMock()

            edge_repo = MockEdgeRepo.return_value
            edge_repo.get_edges_for_node = AsyncMock(return_value=[edge])
            edge_repo.upsert = AsyncMock()
            edge_repo.delete_by_key = AsyncMock()

            state = _make_state(write_sf=lambda: _FakeSessionContext(ws))
            result = await _absorb_merged_nodes(state, _make_settings(), _make_ctx())

        assert result == 1
        # Old edge deleted, new edge upserted under winner key
        edge_repo.delete_by_key.assert_called_once_with(edge.key)
        edge_repo.upsert.assert_called_once()
        upsert_call = edge_repo.upsert.call_args
        assert upsert_call.kwargs["source_node_key"] == "new"

    async def test_error_in_one_seed_does_not_block_others(self):
        """An error absorbing one seed should not prevent others from being processed."""
        from kt_worker_nodes.workflows.auto_build import _absorb_merged_nodes

        seed_ok = _make_seed(
            "ok",
            "Ok",
            promoted_node_key="ok",
            merged_into_key="winner",
        )
        seed_err = _make_seed(
            "err",
            "Err",
            promoted_node_key="err",
            merged_into_key="winner",
        )
        winner_seed = _make_seed(
            "winner",
            "Winner",
            status="promoted",
            promoted_node_key="winner",
        )
        ok_node = _make_write_node("ok", "Ok", fact_ids=[])
        err_node = _make_write_node("err", "Err", fact_ids=[])
        winner_node = _make_write_node("winner", "Winner")

        ws = MagicMock()
        ws.commit = AsyncMock()

        with (
            patch(_SEED_REPO) as MockSeedRepo,
            patch(_NODE_REPO) as MockNodeRepo,
            patch(_EDGE_REPO) as MockEdgeRepo,
            patch(_DIM_REPO) as MockDimRepo,
        ):
            seed_repo = MockSeedRepo.return_value
            seed_repo.get_merged_promoted_seeds = AsyncMock(return_value=[seed_err, seed_ok])
            seed_repo.get_seed_by_key = AsyncMock(return_value=winner_seed)
            seed_repo.clear_promoted_node_key = AsyncMock()

            node_repo = MockNodeRepo.return_value
            node_repo.get_by_key = AsyncMock(
                side_effect=lambda k: {
                    "winner": winner_node,
                    "ok": ok_node,
                    "err": err_node,
                }.get(k)
            )
            node_repo.merge_fact_ids = AsyncMock()
            node_repo.delete_by_key = AsyncMock()

            dim_repo = MockDimRepo.return_value
            # First call (for err seed) raises, second (for ok seed) succeeds
            dim_repo.get_by_node_key = AsyncMock(side_effect=[Exception("boom"), []])
            dim_repo.delete_convergence_report = AsyncMock()
            dim_repo.delete_divergent_claims = AsyncMock()

            edge_repo = MockEdgeRepo.return_value
            edge_repo.get_edges_for_node = AsyncMock(return_value=[])

            state = _make_state(write_sf=lambda: _FakeSessionContext(ws))
            result = await _absorb_merged_nodes(state, _make_settings(), _make_ctx())

        # Only the successful one counted
        assert result == 1
