"""Tests for the AncestryPipeline merge algorithm and seed creation."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kt_config.types import ALL_CONCEPTS_ID
from kt_ontology.ancestry import AncestryPipeline, AncestryResult
from kt_ontology.base import AncestorEntry, AncestryChain
from kt_ontology.registry import OntologyProviderRegistry

# ── Fixtures ─────────────────────────────────────────────────────


def _make_pipeline() -> AncestryPipeline:
    """Create an AncestryPipeline with mocked dependencies."""
    session = AsyncMock()
    model_gateway = MagicMock()
    embedding_service = MagicMock()
    ontology_registry = OntologyProviderRegistry()
    return AncestryPipeline(
        session=session,
        model_gateway=model_gateway,
        embedding_service=embedding_service,
        ontology_registry=ontology_registry,
    )


def _entry(name: str, desc: str | None = None, ext_id: str | None = None) -> AncestorEntry:
    return AncestorEntry(name=name, description=desc, external_id=ext_id)


def _chain(entries: list[AncestorEntry], source: str = "test") -> AncestryChain:
    return AncestryChain(ancestors=entries, source=source)


# ── Merge algorithm tests ────────────────────────────────────────


class TestMergeChains:
    """Tests for _merge_chains."""

    def test_both_none(self) -> None:
        pipeline = _make_pipeline()
        result = pipeline._merge_chains(None, None, "test")
        assert result == []

    def test_ai_only(self) -> None:
        pipeline = _make_pipeline()
        ai = _chain([_entry("sorting algorithms"), _entry("algorithms"), _entry("all concepts")], source="ai")
        result = pipeline._merge_chains(ai, None, "quicksort")
        assert len(result) == 3
        assert result[0].name == "sorting algorithms"
        assert result[2].name == "all concepts"

    def test_base_only(self) -> None:
        pipeline = _make_pipeline()
        base = _chain([_entry("algorithm"), _entry("mathematical object")], source="wikidata")
        result = pipeline._merge_chains(None, base, "quicksort")
        assert len(result) == 2
        assert result[0].name == "algorithm"

    def test_ai_and_base_agree(self) -> None:
        """When both chains share a common ancestor, merge should include it."""
        pipeline = _make_pipeline()
        ai = _chain(
            [
                _entry("sorting algorithms"),
                _entry("algorithms"),
                _entry("computer science"),
                _entry("all concepts"),
            ],
            source="ai",
        )
        base = _chain(
            [
                _entry("algorithm"),
                _entry("computer science"),
                _entry("formal science"),
            ],
            source="wikidata",
        )

        result = pipeline._merge_chains(ai, base, "quicksort")
        names = [r.name.lower() for r in result]
        # Should contain "computer science" from both
        assert "computer science" in names

    def test_ai_and_base_diverge_no_common(self) -> None:
        """When chains share no ancestors, AI chain is preferred with base appended."""
        pipeline = _make_pipeline()
        ai = _chain(
            [
                _entry("sorting algorithms"),
                _entry("algorithms"),
            ],
            source="ai",
        )
        base = _chain(
            [
                _entry("mathematical object"),
                _entry("abstract entity"),
            ],
            source="wikidata",
        )

        result = pipeline._merge_chains(ai, base, "quicksort")
        names = [r.name.lower() for r in result]
        # AI chain items should come first
        assert names[0] == "sorting algorithms"
        assert names[1] == "algorithms"
        # Base items appended
        assert "mathematical object" in names
        assert "abstract entity" in names

    def test_deduplication(self) -> None:
        """Duplicate names across chains should be deduplicated."""
        pipeline = _make_pipeline()
        ai = _chain([_entry("A"), _entry("B"), _entry("C")], source="ai")
        base = _chain([_entry("B"), _entry("C"), _entry("D")], source="wikidata")

        result = pipeline._merge_chains(ai, base, "test")
        names = [r.name for r in result]
        # No duplicates
        assert len(names) == len(set(n.lower() for n in names))


class TestDetermineAncestryEntity:
    """Entity nodes skip deep ontology but get a root parent."""

    @pytest.mark.asyncio
    async def test_entity_gets_root_parent(self) -> None:
        from kt_config.types import ALL_ENTITIES_ID

        pipeline = _make_pipeline()
        result = await pipeline.determine_ancestry("Elon Musk", "entity")
        assert isinstance(result, AncestryResult)
        assert result.parent_id == ALL_ENTITIES_ID
        assert result.nodes_created == []
        assert result.ancestry_chain == [ALL_ENTITIES_ID]

    @pytest.mark.asyncio
    async def test_disabled_feature_flag(self) -> None:
        """When enable_ontology_ancestry is False, use default parent."""
        pipeline = _make_pipeline()
        with patch("kt_ontology.ancestry.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                enable_ontology_ancestry=False,
                ontology_similarity_threshold=0.85,
            )
            result = await pipeline.determine_ancestry("quicksort", "concept")
        assert result.parent_id == ALL_CONCEPTS_ID
        assert result.nodes_created == []


class TestGraphEngineSetParentGuard:
    """GraphEngine.set_parent must reject invalid parent chains."""

    @pytest.mark.asyncio
    async def test_set_parent_rejects_self_reference(self) -> None:
        """set_parent(node_id, node_id) should raise ValueError."""
        from kt_graph.engine import GraphEngine

        session = AsyncMock()
        embedding_service = MagicMock()
        engine = GraphEngine(session, embedding_service)

        node_id = uuid.uuid4()
        with pytest.raises(ValueError, match="Cannot set node .* as its own parent"):
            await engine.set_parent(node_id, node_id)

    @pytest.mark.asyncio
    async def test_set_parent_allows_root_parent(self) -> None:
        """set_parent with a root ID should always succeed."""
        from kt_graph.engine import GraphEngine

        session = AsyncMock()
        embedding_service = MagicMock()
        engine = GraphEngine(session, embedding_service)

        node_id = uuid.uuid4()
        # Root IDs are always valid — no chain walk needed
        await engine.set_parent(node_id, ALL_CONCEPTS_ID)

    @pytest.mark.asyncio
    async def test_set_parent_allows_chain_reaching_root(self) -> None:
        """set_parent succeeds when parent chain reaches a root node."""
        from kt_graph.engine import GraphEngine

        session = AsyncMock()
        embedding_service = MagicMock()
        engine = GraphEngine(session, embedding_service)

        node_id = uuid.uuid4()
        parent_id = uuid.uuid4()
        grandparent_id = uuid.uuid4()

        nodes = {
            parent_id: MagicMock(id=parent_id, parent_id=grandparent_id),
            grandparent_id: MagicMock(id=grandparent_id, parent_id=ALL_CONCEPTS_ID),
        }
        engine._node_repo.get_by_id = AsyncMock(side_effect=lambda nid: nodes.get(nid))

        await engine.set_parent(node_id, parent_id)

    @pytest.mark.asyncio
    async def test_set_parent_rejects_cycle(self) -> None:
        """set_parent should detect cycles in the ancestor chain."""
        from kt_graph.engine import GraphEngine

        session = AsyncMock()
        embedding_service = MagicMock()
        engine = GraphEngine(session, embedding_service)

        node_a = uuid.uuid4()
        node_b = uuid.uuid4()
        node_c = uuid.uuid4()

        # A → B → C → (no root)
        nodes = {
            node_a: MagicMock(id=node_a, parent_id=node_b),
            node_b: MagicMock(id=node_b, parent_id=node_c),
            node_c: MagicMock(id=node_c, parent_id=ALL_CONCEPTS_ID),
        }
        engine._node_repo.get_by_id = AsyncMock(side_effect=lambda nid: nodes.get(nid))

        # Setting C.parent = A would create A → B → C → A cycle
        with pytest.raises(ValueError, match="would create a cycle"):
            await engine.set_parent(node_c, node_a)

    @pytest.mark.asyncio
    async def test_set_parent_rejects_chain_not_reaching_root(self) -> None:
        """set_parent rejects if parent chain ends without reaching a root."""
        from kt_graph.engine import GraphEngine

        session = AsyncMock()
        embedding_service = MagicMock()
        engine = GraphEngine(session, embedding_service)

        node_id = uuid.uuid4()
        orphan_parent = uuid.uuid4()

        # orphan_parent has no parent (None) and is not a root node
        nodes = {
            orphan_parent: MagicMock(id=orphan_parent, parent_id=None),
        }
        engine._node_repo.get_by_id = AsyncMock(side_effect=lambda nid: nodes.get(nid))

        with pytest.raises(ValueError, match="not a root node"):
            await engine.set_parent(node_id, orphan_parent)

    @pytest.mark.asyncio
    async def test_validate_chain_ok(self) -> None:
        """_validate_parent_chain returns ok for valid chain to root."""
        from kt_graph.engine import GraphEngine

        session = AsyncMock()
        embedding_service = MagicMock()
        engine = GraphEngine(session, embedding_service)

        node_a = uuid.uuid4()
        node_b = uuid.uuid4()

        nodes = {
            node_b: MagicMock(id=node_b, parent_id=ALL_CONCEPTS_ID),
        }
        engine._node_repo.get_by_id = AsyncMock(side_effect=lambda nid: nodes.get(nid))

        ok, reason = await engine._validate_parent_chain(node_a, node_b)
        assert ok is True


# ── Seed creation tests ─────────────────────────────────────


class TestEnsureSeedForNode:
    """Tests for _ensure_seed_for_node — ancestry nodes must have seeds."""

    @pytest.mark.asyncio
    async def test_creates_seed_for_new_node(self) -> None:
        """A new ancestry node should get a seed with fact_count=0."""
        pipeline = _make_pipeline()
        mock_repo = AsyncMock()
        mock_repo.upsert_seeds_batch = AsyncMock()

        await pipeline._ensure_seed_for_node(
            "Quantum Computing",
            "concept",
            None,
            mock_repo,
            None,
        )

        mock_repo.upsert_seeds_batch.assert_called_once()
        call_args = mock_repo.upsert_seeds_batch.call_args[0][0]
        assert len(call_args) == 1
        assert call_args[0]["name"] == "Quantum Computing"
        assert call_args[0]["node_type"] == "concept"
        assert call_args[0]["fact_count"] == 0
        assert call_args[0]["key"] == "concept:quantum-computing"

    @pytest.mark.asyncio
    async def test_noop_without_write_repo(self) -> None:
        """No error when write_seed_repo is None."""
        pipeline = _make_pipeline()
        # Should not raise
        await pipeline._ensure_seed_for_node(
            "Test",
            "concept",
            None,
            None,
            None,
        )

    @pytest.mark.asyncio
    async def test_upserts_embedding_to_qdrant(self) -> None:
        """When embedding and qdrant_seed_repo are provided, upserts to Qdrant."""
        pipeline = _make_pipeline()
        mock_repo = AsyncMock()
        mock_qdrant = AsyncMock()
        embedding = [0.1] * 10

        await pipeline._ensure_seed_for_node(
            "Physics",
            "concept",
            embedding,
            mock_repo,
            mock_qdrant,
        )

        mock_qdrant.upsert.assert_called_once_with(
            seed_key="concept:physics",
            embedding=embedding,
            name="Physics",
            node_type="concept",
        )

    @pytest.mark.asyncio
    async def test_handles_repo_error_gracefully(self) -> None:
        """Seed creation errors are caught — should not break ancestry."""
        pipeline = _make_pipeline()
        mock_repo = AsyncMock()
        mock_repo.upsert_seeds_batch = AsyncMock(side_effect=RuntimeError("db error"))

        # Should not raise
        await pipeline._ensure_seed_for_node(
            "Test",
            "concept",
            None,
            mock_repo,
            None,
        )
