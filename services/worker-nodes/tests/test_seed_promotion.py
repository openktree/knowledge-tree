"""Tests for seed-aware node promotion in the pipeline."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kt_db.keys import make_seed_key
from kt_worker_nodes.pipelines.models import CreateNodeTask
from kt_worker_nodes.pipelines.nodes.pipeline import NodeCreationPipeline


def _make_ctx(write_session=None):
    """Build a minimal AgentContext mock."""
    ctx = MagicMock()
    ctx.graph_engine = MagicMock()
    ctx.graph_engine._write_session = write_session or MagicMock()
    ctx.graph_engine.search_nodes_by_trigram = AsyncMock(return_value=[])
    ctx.graph_engine.find_similar_nodes = AsyncMock(return_value=[])
    ctx.graph_engine.search_fact_pool = AsyncMock(return_value=[])
    ctx.graph_engine.search_fact_pool_text = AsyncMock(return_value=[])
    ctx.graph_engine.get_facts_by_ids = AsyncMock(return_value=[])
    ctx.graph_engine.create_node = AsyncMock()
    ctx.graph_engine.link_fact_to_node = AsyncMock()
    ctx.graph_engine.increment_access_count = AsyncMock()
    ctx.graph_engine.is_node_stale = MagicMock(return_value=False)
    ctx.embedding_service = MagicMock()
    ctx.embedding_service.embed_text = AsyncMock(return_value=[0.1] * 10)
    ctx.session = MagicMock()
    ctx.session.commit = AsyncMock()
    ctx.model_gateway = MagicMock()
    ctx.qdrant_client = None
    ctx.emit = AsyncMock()
    return ctx


def _make_state(query: str = "test query"):
    """Build a minimal OrchestratorState mock."""
    state = MagicMock()
    state.query = query
    state.explore_remaining = 10
    state.explore_used = 0
    state.nav_used = 0
    state.visited_nodes = []
    state.created_nodes = []
    state.exploration_path = []
    state.disable_external_search = False
    return state


def _make_fact(fact_id: uuid.UUID | None = None, content: str = "test fact") -> MagicMock:
    fact = MagicMock()
    fact.id = fact_id or uuid.uuid4()
    fact.content = content
    fact.fact_type = "claim"
    fact.embedding = None
    return fact


def _make_seed(key: str, name: str, node_type: str, status: str = "active", fact_count: int = 5) -> MagicMock:
    seed = MagicMock()
    seed.key = key
    seed.name = name
    seed.node_type = node_type
    seed.status = status
    seed.fact_count = fact_count
    return seed


# ── Seed-aware classification ──────────────────────────────────────


@pytest.mark.asyncio
class TestSeedAwareClassification:
    async def test_seed_with_enough_facts_skips_pool_search(self):
        """When a seed has enough facts, classify uses seed facts directly."""
        ctx = _make_ctx()
        pipeline = NodeCreationPipeline(ctx)
        state = _make_state()

        task = CreateNodeTask(
            name="Albert Einstein",
            node_type="entity",
            seed_key=make_seed_key("Albert Einstein"),
            entity_subtype="person",
        )
        seed_key = make_seed_key("Albert Einstein")

        facts = [_make_fact() for _ in range(10)]
        seed = _make_seed(seed_key, "Albert Einstein", "entity", fact_count=10)

        with patch("kt_db.repositories.write_seeds.WriteSeedRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_seed_by_key = AsyncMock(return_value=seed)
            mock_repo.get_facts_for_seed = AsyncMock(return_value=[f.id for f in facts])
            ctx.graph_engine.get_facts_by_ids = AsyncMock(return_value=facts)

            await pipeline._classify_task(task, state)

        assert task.action == "create"
        assert len(task.pool_facts) == 10
        # Should NOT have searched the fact pool (seed provided facts)
        ctx.graph_engine.search_fact_pool.assert_not_called()

    async def test_seed_below_threshold_falls_through(self):
        """When seed has too few facts, fall through to external search (no pool search)."""
        ctx = _make_ctx()
        pipeline = NodeCreationPipeline(ctx)
        state = _make_state()

        task = CreateNodeTask(
            name="Obscure Topic", node_type="concept", seed_key=make_seed_key("Obscure Topic")
        )
        task.embedding = [0.1] * 10
        seed_key = make_seed_key("Obscure Topic")

        seed = _make_seed(seed_key, "Obscure Topic", "concept", fact_count=1)

        with patch("kt_db.repositories.write_seeds.WriteSeedRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_seed_by_key = AsyncMock(return_value=seed)

            await pipeline._classify_task(task, state)

        # No pool search — seeds are the sole source of facts
        ctx.graph_engine.search_fact_pool.assert_not_called()
        # Should charge explore budget for external search
        assert task.explore_charged is True

    async def test_no_seed_falls_through(self):
        """When no seed exists, fall through to external search (no pool search)."""
        ctx = _make_ctx()
        pipeline = NodeCreationPipeline(ctx)
        state = _make_state()

        task = CreateNodeTask(name="New Topic", node_type="concept", seed_key=make_seed_key("New Topic"))
        task.embedding = [0.1] * 10

        with patch("kt_db.repositories.write_seeds.WriteSeedRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_seed_by_key = AsyncMock(return_value=None)

            await pipeline._classify_task(task, state)

        # No pool search — seeds are the sole source of facts
        ctx.graph_engine.search_fact_pool.assert_not_called()
        assert task.explore_charged is True

    async def test_no_write_session_falls_through(self):
        """When no write session, skip seed check and go to external search."""
        ctx = _make_ctx()
        ctx.graph_engine._write_session = None
        pipeline = NodeCreationPipeline(ctx)
        state = _make_state()

        task = CreateNodeTask(
            name="No Write Session", node_type="concept", seed_key=make_seed_key("No Write Session")
        )
        task.embedding = [0.1] * 10

        await pipeline._classify_task(task, state)

        # No pool search — seeds are the sole source of facts
        ctx.graph_engine.search_fact_pool.assert_not_called()
        assert task.explore_charged is True

    async def test_seed_lookup_exception_falls_through(self):
        """If seed lookup raises, fall through gracefully to external search."""
        ctx = _make_ctx()
        pipeline = NodeCreationPipeline(ctx)
        state = _make_state()

        task = CreateNodeTask(name="Error Topic", node_type="concept", seed_key=make_seed_key("Error Topic"))
        task.embedding = [0.1] * 10

        with patch("kt_db.repositories.write_seeds.WriteSeedRepository") as MockRepo:
            MockRepo.side_effect = Exception("DB connection error")

            await pipeline._classify_task(task, state)

        # No pool search — seeds are the sole source of facts
        ctx.graph_engine.search_fact_pool.assert_not_called()
        assert task.explore_charged is True

    async def test_seed_with_merged_status_falls_through(self):
        """A merged seed should not be used for promotion — goes to external search."""
        ctx = _make_ctx()
        pipeline = NodeCreationPipeline(ctx)
        state = _make_state()

        task = CreateNodeTask(
            name="Merged Topic", node_type="concept", seed_key=make_seed_key("Merged Topic")
        )
        task.embedding = [0.1] * 10
        seed_key = make_seed_key("Merged Topic")

        seed = _make_seed(seed_key, "Merged Topic", "concept", status="merged", fact_count=10)

        with patch("kt_db.repositories.write_seeds.WriteSeedRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_seed_by_key = AsyncMock(return_value=seed)

            await pipeline._classify_task(task, state)

        # No pool search — seeds are the sole source of facts
        ctx.graph_engine.search_fact_pool.assert_not_called()
        assert task.explore_charged is True

    async def test_seed_facts_empty_after_load_falls_through(self):
        """If seed has fact_count but get_facts_by_ids returns empty, go to external search."""
        ctx = _make_ctx()
        pipeline = NodeCreationPipeline(ctx)
        state = _make_state()

        task = CreateNodeTask(
            name="Empty Facts Topic", node_type="concept", seed_key=make_seed_key("Empty Facts Topic")
        )
        task.embedding = [0.1] * 10
        seed_key = make_seed_key("Empty Facts Topic")

        seed = _make_seed(seed_key, "Empty Facts Topic", "concept", fact_count=5)

        with patch("kt_db.repositories.write_seeds.WriteSeedRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_seed_by_key = AsyncMock(return_value=seed)
            mock_repo.get_facts_for_seed = AsyncMock(return_value=[uuid.uuid4() for _ in range(5)])
            ctx.graph_engine.get_facts_by_ids = AsyncMock(return_value=[])

            await pipeline._classify_task(task, state)

        # No pool search — seeds are the sole source of facts
        ctx.graph_engine.search_fact_pool.assert_not_called()
        assert task.explore_charged is True


# ── Seed promotion on node creation ────────────────────────────────


@pytest.mark.asyncio
class TestSeedPromotion:
    async def test_seed_promoted_on_node_create(self):
        """After node creation, the corresponding seed should be promoted."""
        ctx = _make_ctx()
        pipeline = NodeCreationPipeline(ctx)
        state = _make_state()

        task = CreateNodeTask(
            name="Quantum Physics", node_type="concept", seed_key=make_seed_key("Quantum Physics")
        )
        task.pool_facts = [_make_fact() for _ in range(3)]

        node = MagicMock()
        node.id = uuid.uuid4()
        node.concept = "Quantum Physics"
        node.node_type = "concept"
        node.embedding = [0.1] * 10
        ctx.graph_engine.create_node = AsyncMock(return_value=node)

        with patch("kt_db.repositories.write_seeds.WriteSeedRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.promote_seed = AsyncMock(return_value=True)

            await pipeline._handle_create(task, state)

        # Verify promote_seed was called
        seed_key = make_seed_key("Quantum Physics")
        MockRepo.return_value.promote_seed.assert_called_once_with(seed_key, seed_key)

    async def test_promotion_failure_is_non_fatal(self):
        """If seed promotion fails, node creation still succeeds."""
        ctx = _make_ctx()
        pipeline = NodeCreationPipeline(ctx)
        state = _make_state()

        task = CreateNodeTask(
            name="Resilient Node", node_type="concept", seed_key=make_seed_key("Resilient Node")
        )
        task.pool_facts = [_make_fact()]

        node = MagicMock()
        node.id = uuid.uuid4()
        node.concept = "Resilient Node"
        node.node_type = "concept"
        ctx.graph_engine.create_node = AsyncMock(return_value=node)

        with patch("kt_db.repositories.write_seeds.WriteSeedRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.promote_seed = AsyncMock(side_effect=Exception("DB error"))

            await pipeline._handle_create(task, state)

        # Node should still be created successfully
        assert task.node == node
        assert str(node.id) in state.created_nodes

    async def test_no_write_session_skips_promotion(self):
        """When no write session, seed promotion is skipped gracefully."""
        ctx = _make_ctx()
        ctx.graph_engine._write_session = None
        pipeline = NodeCreationPipeline(ctx)
        state = _make_state()

        task = CreateNodeTask(
            name="No Session Node", node_type="concept", seed_key=make_seed_key("No Session Node")
        )
        task.pool_facts = [_make_fact()]

        node = MagicMock()
        node.id = uuid.uuid4()
        node.concept = "No Session Node"
        node.node_type = "concept"
        ctx.graph_engine.create_node = AsyncMock(return_value=node)

        await pipeline._handle_create(task, state)

        # Node should still be created
        assert task.node == node
        assert str(node.id) in state.created_nodes

    async def test_no_pool_facts_skips_creation(self):
        """If no pool facts, _handle_create skips and does not attempt promotion."""
        ctx = _make_ctx()
        pipeline = NodeCreationPipeline(ctx)
        state = _make_state()

        task = CreateNodeTask(name="Empty Node", node_type="concept", seed_key=make_seed_key("Empty Node"))
        task.pool_facts = []

        await pipeline._handle_create(task, state)

        assert task.action == "skip"
        ctx.graph_engine.create_node.assert_not_called()


# ── Seed metadata refresh on node refresh ──────────────────────────


def _make_node(concept: str = "Test", node_type: str = "concept") -> MagicMock:
    node = MagicMock()
    node.id = uuid.uuid4()
    node.concept = concept
    node.node_type = node_type
    node.metadata_ = {}
    node.embedding = [0.1] * 10
    return node


def _make_route(parent_key: str, child_key: str, label: str, ambiguity_type: str = "polysemy") -> MagicMock:
    route = MagicMock()
    route.parent_seed_key = parent_key
    route.child_seed_key = child_key
    route.label = label
    route.ambiguity_type = ambiguity_type
    return route


def _make_merge(source_key: str, target_key: str, operation: str = "merge") -> MagicMock:
    merge = MagicMock()
    merge.source_seed_key = source_key
    merge.target_seed_key = target_key
    merge.operation = operation
    return merge


@pytest.mark.asyncio
class TestRefreshSeedMetadata:
    async def test_refresh_updates_aliases_and_merged_names(self):
        """On refresh, seed aliases and merged names should be written to node metadata."""
        ctx = _make_ctx()
        node = _make_node("Machine Learning", "concept")
        seed_key = make_seed_key("Machine Learning")
        seed = _make_seed(seed_key, "Machine Learning", "concept", status="active", fact_count=3)
        seed.metadata_ = {"aliases": ["ML", "Statistical Learning"]}

        merge_src = _make_seed("deep-learning", "Deep Learning", "concept")
        merge = _make_merge("deep-learning", seed_key)

        seed_fact_ids = [uuid.uuid4(), uuid.uuid4()]
        seed_facts = [_make_fact(fid) for fid in seed_fact_ids]

        pipeline = NodeCreationPipeline(ctx)
        task = CreateNodeTask(name="Machine Learning", node_type="concept", seed_key=seed_key)
        task.existing_node = node
        task.action = "refresh"

        with patch("kt_db.repositories.write_seeds.WriteSeedRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_seed_by_key = AsyncMock(
                side_effect=lambda k: {
                    seed_key: seed,
                    "deep-learning": merge_src,
                }.get(k)
            )
            mock_repo.get_merges_for_seed = AsyncMock(return_value=[merge])
            mock_repo.get_facts_for_seed = AsyncMock(return_value=seed_fact_ids)
            mock_repo.get_route_for_child = AsyncMock(return_value=None)
            ctx.graph_engine.get_facts_by_ids = AsyncMock(return_value=seed_facts)
            ctx.graph_engine.update_node = AsyncMock(return_value=node)

            await pipeline._refresh_seed_metadata(task)

        # Verify update_node was called with aliases and merged_from
        ctx.graph_engine.update_node.assert_called_once()
        call_kwargs = ctx.graph_engine.update_node.call_args
        meta = call_kwargs.kwargs.get("metadata_") or call_kwargs[1].get("metadata_")
        assert meta["aliases"] == ["ML", "Statistical Learning"]
        assert meta["merged_from"] == ["Deep Learning"]

        # Verify seed facts were linked
        assert ctx.graph_engine.link_fact_to_node.call_count == 2

        # Verify seed_context set for dimension generation
        assert task.seed_context is not None
        assert "ML" in task.seed_context

    async def test_refresh_ambiguous_seed_links_descendant_facts(self):
        """Ambiguous seeds should aggregate facts from sub-seeds on refresh."""
        ctx = _make_ctx()
        node = _make_node("Python", "concept")
        seed_key = make_seed_key("Python")
        seed = _make_seed(seed_key, "Python", "concept", status="ambiguous", fact_count=10)
        seed.metadata_ = {}

        child_route_1 = _make_route(seed_key, "python-lang", "Python (programming language)")
        child_route_2 = _make_route(seed_key, "python-snake", "Python (snake)")

        descendant_fact_ids = [uuid.uuid4() for _ in range(6)]
        descendant_facts = [_make_fact(fid) for fid in descendant_fact_ids]

        pipeline = NodeCreationPipeline(ctx)
        task = CreateNodeTask(name="Python", node_type="concept", seed_key=seed_key)
        task.existing_node = node
        task.action = "refresh"

        with patch("kt_db.repositories.write_seeds.WriteSeedRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_seed_by_key = AsyncMock(return_value=seed)
            mock_repo.get_merges_for_seed = AsyncMock(return_value=[])
            mock_repo.get_all_descendant_facts = AsyncMock(return_value=descendant_fact_ids)
            mock_repo.get_routes_for_parent = AsyncMock(return_value=[child_route_1, child_route_2])
            ctx.graph_engine.get_facts_by_ids = AsyncMock(return_value=descendant_facts)
            ctx.graph_engine.update_node = AsyncMock(return_value=node)

            await pipeline._refresh_seed_metadata(task)

        # Verify descendant facts were linked
        assert ctx.graph_engine.link_fact_to_node.call_count == 6

        # Verify seed_context mentions sub-seeds
        assert task.seed_context is not None
        assert "Python (programming language)" in task.seed_context
        assert "Python (snake)" in task.seed_context

        # Verify ambiguity metadata stored
        ctx.graph_engine.update_node.assert_called_once()
        call_kwargs = ctx.graph_engine.update_node.call_args
        meta = call_kwargs.kwargs.get("metadata_") or call_kwargs[1].get("metadata_")
        assert meta["seed_ambiguity"]["ambiguity_type"] == "parent"
        assert "Python (programming language)" in meta["seed_ambiguity"]["child_names"]

    async def test_refresh_disambiguated_child_stores_sibling_info(self):
        """A disambiguated child seed should store parent/sibling info on refresh."""
        ctx = _make_ctx()
        node = _make_node("Python (programming language)", "concept")
        child_key = make_seed_key("Python (programming language)")
        parent_key = make_seed_key("Python")

        seed = _make_seed(child_key, "Python (programming language)", "concept", status="active", fact_count=5)
        seed.metadata_ = {}
        parent_seed = _make_seed(parent_key, "Python", "concept", status="ambiguous")

        child_route = _make_route(parent_key, child_key, "Python (programming language)")
        sibling_route = _make_route(parent_key, "python-snake", "Python (snake)")
        sibling_seed = _make_seed("python-snake", "Python (snake)", "concept")

        seed_fact_ids = [uuid.uuid4()]
        seed_facts = [_make_fact(seed_fact_ids[0])]

        pipeline = NodeCreationPipeline(ctx)
        task = CreateNodeTask(name="Python (programming language)", node_type="concept", seed_key=child_key)
        task.existing_node = node
        task.action = "refresh"

        with patch("kt_db.repositories.write_seeds.WriteSeedRepository") as MockRepo:
            mock_repo = MockRepo.return_value
            mock_repo.get_seed_by_key = AsyncMock(
                side_effect=lambda k: {
                    child_key: seed,
                    parent_key: parent_seed,
                    "python-snake": sibling_seed,
                }.get(k)
            )
            mock_repo.get_merges_for_seed = AsyncMock(return_value=[])
            mock_repo.get_facts_for_seed = AsyncMock(return_value=seed_fact_ids)
            mock_repo.get_route_for_child = AsyncMock(return_value=child_route)
            mock_repo.get_routes_for_parent = AsyncMock(return_value=[child_route, sibling_route])
            ctx.graph_engine.get_facts_by_ids = AsyncMock(return_value=seed_facts)
            ctx.graph_engine.update_node = AsyncMock(return_value=node)

            await pipeline._refresh_seed_metadata(task)

        ctx.graph_engine.update_node.assert_called_once()
        call_kwargs = ctx.graph_engine.update_node.call_args
        meta = call_kwargs.kwargs.get("metadata_") or call_kwargs[1].get("metadata_")
        assert meta["seed_ambiguity"]["is_disambiguated"] is True
        assert meta["seed_ambiguity"]["parent_name"] == "Python"
        assert "Python (snake)" in meta["seed_ambiguity"]["sibling_names"]

    async def test_refresh_no_write_session_skips(self):
        """No write session means seed metadata refresh is a no-op."""
        ctx = _make_ctx()
        ctx.graph_engine._write_session = None
        node = _make_node()

        pipeline = NodeCreationPipeline(ctx)
        task = CreateNodeTask(name="Test", node_type="concept", seed_key=make_seed_key("Test"))
        task.existing_node = node

        await pipeline._refresh_seed_metadata(task)

        ctx.graph_engine.update_node.assert_not_called()

    async def test_refresh_seed_metadata_failure_is_non_fatal(self):
        """If seed metadata refresh fails, it should not propagate."""
        ctx = _make_ctx()
        node = _make_node()

        pipeline = NodeCreationPipeline(ctx)
        task = CreateNodeTask(name="Failing", node_type="concept", seed_key=make_seed_key("Failing"))
        task.existing_node = node

        with patch("kt_db.repositories.write_seeds.WriteSeedRepository") as MockRepo:
            MockRepo.side_effect = Exception("Connection error")

            # Should not raise
            await pipeline._refresh_seed_metadata(task)

        ctx.graph_engine.update_node.assert_not_called()
