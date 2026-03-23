"""Integration test for the AncestryPipeline with a real DB session.

Uses mocked LLM and Wikidata to test the full pipeline flow:
- AI proposes ancestry chain
- Wikidata returns base chain
- Pipeline merges, resolves against system graph, creates stub nodes, wires parents

Run with: uv run pytest tests/integration/test_ancestry_pipeline.py -v -s
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.models import Node
from kt_db.repositories.nodes import NodeRepository
from kt_ontology.ancestry import AncestryPipeline, AncestryResult, _ResolvedAncestor
from kt_ontology.base import AncestorEntry, AncestryChain
from kt_ontology.registry import OntologyProviderRegistry
from kt_config.types import ALL_CONCEPTS_ID


# ── Helpers ──────────────────────────────────────────────────────────────


async def _ensure_root(session: AsyncSession) -> None:
    """Ensure the ALL_CONCEPTS root node exists (idempotent)."""
    from sqlalchemy import select

    existing = await session.execute(
        select(Node).where(Node.id == ALL_CONCEPTS_ID)
    )
    if not existing.scalar_one_or_none():
        session.add(Node(id=ALL_CONCEPTS_ID, concept="all concepts", node_type="concept"))
        await session.flush()


async def _create_node(
    session: AsyncSession,
    concept: str,
    parent_id: uuid.UUID | None = None,
    node_type: str = "concept",
    embedding: list[float] | None = None,
) -> Node:
    repo = NodeRepository(session)
    return await repo.create(
        concept=concept,
        parent_id=parent_id,
        node_type=node_type,
        embedding=embedding,
    )


def _make_embedding(seed: float = 0.1) -> list[float]:
    return [seed] * 3072


def _make_quicksort_chain() -> str:
    """Standard AI response for 'quicksort'."""
    return json.dumps([
        {"name": "quicksort", "description": "A comparison-based sorting algorithm"},
        {"name": "sorting algorithms", "description": "Algorithms that arrange elements in order"},
        {"name": "algorithms", "description": "Step-by-step computational procedures"},
        {"name": "computer science", "description": "The study of computation"},
        {"name": "all concepts", "description": "Root"},
    ])


def _make_photosynthesis_chain() -> str:
    """Standard AI response for 'photosynthesis'."""
    return json.dumps([
        {"name": "photosynthesis", "description": "Converting light to chemical energy"},
        {"name": "plant energy metabolism", "description": "Energy processes in plants"},
        {"name": "plant physiology", "description": "Study of plant functions"},
        {"name": "botany", "description": "Study of plants"},
        {"name": "biology", "description": "Study of life"},
        {"name": "natural sciences", "description": "Sciences studying nature"},
        {"name": "all concepts", "description": "Root"},
    ])


@pytest.fixture
def mock_model_gateway() -> MagicMock:
    gw = MagicMock()
    gw.generate = AsyncMock(return_value="[]")
    return gw


@pytest.fixture
def mock_embedding_service() -> MagicMock:
    svc = MagicMock()
    call_count = [0]

    async def _embed(text: str) -> list[float]:
        call_count[0] += 1
        seed = 0.1 + call_count[0] * 0.001
        return [seed] * 3072

    svc.embed_text = AsyncMock(side_effect=_embed)
    return svc


@pytest.fixture
def ontology_registry() -> OntologyProviderRegistry:
    return OntologyProviderRegistry()


def _mock_registry_with_wikidata(
    base_chain: AncestryChain | None = None,
) -> OntologyProviderRegistry:
    """Create registry with a mock Wikidata provider."""
    registry = OntologyProviderRegistry()
    provider = MagicMock()
    provider.provider_id = "mock-wikidata"
    provider.is_available = AsyncMock(return_value=True)
    provider.get_ancestry = AsyncMock(return_value=base_chain)
    registry.register(provider, default=True)
    return registry


# ── Step-by-step diagnostic tests ────────────────────────────────────────


@pytest.mark.asyncio
class TestAncestrySteps:
    """Test each pipeline step independently with diagnostic output."""

    async def test_step1_get_existing_ancestor_names(
        self, db_session: AsyncSession, mock_model_gateway: MagicMock,
        mock_embedding_service: MagicMock, ontology_registry: OntologyProviderRegistry,
    ) -> None:
        """Step 1: Find existing nodes that could be ancestors."""
        await _ensure_root(db_session)
        await _create_node(db_session, "biology-s1", parent_id=ALL_CONCEPTS_ID)
        await _create_node(db_session, "chemistry-s1", parent_id=ALL_CONCEPTS_ID)

        pipeline = AncestryPipeline(
            session=db_session,
            model_gateway=mock_model_gateway,
            embedding_service=mock_embedding_service,
            ontology_registry=ontology_registry,
        )

        names = await pipeline._get_existing_ancestor_names("concept", limit=30)
        assert len(names) >= 2

    async def test_step2_ai_ancestry_valid_json(
        self, db_session: AsyncSession, mock_model_gateway: MagicMock,
        mock_embedding_service: MagicMock, ontology_registry: OntologyProviderRegistry,
    ) -> None:
        """Step 2: AI returns valid JSON ancestry chain."""
        mock_model_gateway.generate = AsyncMock(
            return_value=_make_quicksort_chain()
        )

        pipeline = AncestryPipeline(
            session=db_session,
            model_gateway=mock_model_gateway,
            embedding_service=mock_embedding_service,
            ontology_registry=ontology_registry,
        )

        chain = await pipeline._get_ai_ancestry(
            "quicksort", "concept", "A sorting algorithm", ["computer science"],
        )
        assert chain is not None
        assert chain.source == "ai"
        names = [a.name for a in chain.ancestors]
        assert "quicksort" not in names
        assert "sorting algorithms" in names
        assert "algorithms" in names

    async def test_step2_ai_ancestry_empty_array(
        self, db_session: AsyncSession, mock_model_gateway: MagicMock,
        mock_embedding_service: MagicMock, ontology_registry: OntologyProviderRegistry,
    ) -> None:
        """Step 2: AI returns empty array → None."""
        mock_model_gateway.generate = AsyncMock(return_value="[]")

        pipeline = AncestryPipeline(
            session=db_session,
            model_gateway=mock_model_gateway,
            embedding_service=mock_embedding_service,
            ontology_registry=ontology_registry,
        )

        chain = await pipeline._get_ai_ancestry("test", "concept", None, [])
        assert chain is None

    async def test_step2_ai_ancestry_single_element(
        self, db_session: AsyncSession, mock_model_gateway: MagicMock,
        mock_embedding_service: MagicMock, ontology_registry: OntologyProviderRegistry,
    ) -> None:
        """Step 2: AI returns array with single element → None (needs ≥2)."""
        mock_model_gateway.generate = AsyncMock(
            return_value=json.dumps([
                {"name": "quicksort", "description": "A sorting algorithm"},
            ])
        )

        pipeline = AncestryPipeline(
            session=db_session,
            model_gateway=mock_model_gateway,
            embedding_service=mock_embedding_service,
            ontology_registry=ontology_registry,
        )

        chain = await pipeline._get_ai_ancestry("quicksort", "concept", None, [])
        assert chain is None

    async def test_step2_ai_ancestry_markdown_fenced(
        self, db_session: AsyncSession, mock_model_gateway: MagicMock,
        mock_embedding_service: MagicMock, ontology_registry: OntologyProviderRegistry,
    ) -> None:
        """Step 2: AI wraps JSON in markdown fences → now handled correctly."""
        fenced = "```json\n" + _make_quicksort_chain() + "\n```"
        mock_model_gateway.generate = AsyncMock(return_value=fenced)

        pipeline = AncestryPipeline(
            session=db_session,
            model_gateway=mock_model_gateway,
            embedding_service=mock_embedding_service,
            ontology_registry=ontology_registry,
        )

        chain = await pipeline._get_ai_ancestry("quicksort", "concept", None, [])
        assert chain is not None, "Markdown fencing should now be handled"
        names = [a.name for a in chain.ancestors]
        assert "sorting algorithms" in names

    async def test_step2_ai_ancestry_llm_exception(
        self, db_session: AsyncSession, mock_model_gateway: MagicMock,
        mock_embedding_service: MagicMock, ontology_registry: OntologyProviderRegistry,
    ) -> None:
        """Step 2: LLM throws → returns None (no crash)."""
        mock_model_gateway.generate = AsyncMock(
            side_effect=Exception("API timeout")
        )

        pipeline = AncestryPipeline(
            session=db_session,
            model_gateway=mock_model_gateway,
            embedding_service=mock_embedding_service,
            ontology_registry=ontology_registry,
        )

        chain = await pipeline._get_ai_ancestry("quicksort", "concept", None, [])
        assert chain is None

    async def test_step4_merge_ai_only(
        self, db_session: AsyncSession, mock_model_gateway: MagicMock,
        mock_embedding_service: MagicMock, ontology_registry: OntologyProviderRegistry,
    ) -> None:
        """Step 4: Merge with only AI chain (Wikidata=None)."""
        pipeline = AncestryPipeline(
            session=db_session,
            model_gateway=mock_model_gateway,
            embedding_service=mock_embedding_service,
            ontology_registry=ontology_registry,
        )

        ai = AncestryChain(
            ancestors=[
                AncestorEntry(name="sorting algorithms"),
                AncestorEntry(name="algorithms"),
                AncestorEntry(name="computer science"),
            ],
            source="ai",
        )
        merged = pipeline._merge_chains(ai, None, "quicksort")
        names = [e.name for e in merged]
        assert names == ["sorting algorithms", "algorithms", "computer science"]

    async def test_step4_merge_both_with_common_ancestor(
        self, db_session: AsyncSession, mock_model_gateway: MagicMock,
        mock_embedding_service: MagicMock, ontology_registry: OntologyProviderRegistry,
    ) -> None:
        """Step 4: Merge AI + Wikidata chains sharing 'botany' as LCA."""
        pipeline = AncestryPipeline(
            session=db_session,
            model_gateway=mock_model_gateway,
            embedding_service=mock_embedding_service,
            ontology_registry=ontology_registry,
        )

        ai = AncestryChain(
            ancestors=[
                AncestorEntry(name="plant energy metabolism"),
                AncestorEntry(name="botany"),
                AncestorEntry(name="biology"),
                AncestorEntry(name="natural sciences"),
            ],
            source="ai",
        )
        base = AncestryChain(
            ancestors=[
                AncestorEntry(name="plant physiology"),
                AncestorEntry(name="botany"),
                AncestorEntry(name="life sciences"),
            ],
            source="wikidata",
        )

        merged = pipeline._merge_chains(ai, base, "photosynthesis")
        names = [e.name for e in merged]
        assert "botany" in names

    async def test_step5_resolve_exact_match(
        self, db_session: AsyncSession, mock_model_gateway: MagicMock,
        mock_embedding_service: MagicMock, ontology_registry: OntologyProviderRegistry,
    ) -> None:
        """Step 5: Resolve finds existing node by exact trigram match."""
        await _ensure_root(db_session)
        bio = await _create_node(
            db_session, "biology-s5",
            parent_id=ALL_CONCEPTS_ID,
            embedding=_make_embedding(0.5),
        )

        pipeline = AncestryPipeline(
            session=db_session,
            model_gateway=mock_model_gateway,
            embedding_service=mock_embedding_service,
            ontology_registry=ontology_registry,
        )

        merged = [
            AncestorEntry(name="plant physiology"),
            AncestorEntry(name="biology-s5"),
        ]

        resolved = await pipeline._resolve_against_system(merged, "concept")

        bio_resolved = [r for r in resolved if r.entry.name == "biology-s5"]
        assert len(bio_resolved) == 1
        assert bio_resolved[0].existing_node_id is not None
        assert bio_resolved[0].existing_node_id == bio.id

    async def test_step5_resolve_excludes_self(
        self, db_session: AsyncSession, mock_model_gateway: MagicMock,
        mock_embedding_service: MagicMock, ontology_registry: OntologyProviderRegistry,
    ) -> None:
        """Step 5: exclude_node_id prevents a node from matching itself."""
        await _ensure_root(db_session)
        algo = await _create_node(
            db_session, "algorithms-excl",
            parent_id=ALL_CONCEPTS_ID,
            embedding=_make_embedding(0.5),
        )

        pipeline = AncestryPipeline(
            session=db_session,
            model_gateway=mock_model_gateway,
            embedding_service=mock_embedding_service,
            ontology_registry=ontology_registry,
        )

        merged = [
            AncestorEntry(name="algorithms-excl"),
            AncestorEntry(name="computer science"),
        ]

        # Without exclusion: should find algorithms-excl
        resolved_no_excl = await pipeline._resolve_against_system(merged, "concept")
        algo_match = [r for r in resolved_no_excl if r.entry.name == "algorithms-excl"]
        assert algo_match[0].existing_node_id == algo.id

        # With exclusion: algorithms-excl should NOT be matched
        resolved_excl = await pipeline._resolve_against_system(
            merged, "concept", exclude_node_id=algo.id
        )
        algo_match_excl = [r for r in resolved_excl if r.entry.name == "algorithms-excl"]
        assert algo_match_excl[0].existing_node_id is None
        assert algo_match_excl[0].needs_creation is True

    async def test_step6_materialize_with_graft(
        self, db_session: AsyncSession, mock_model_gateway: MagicMock,
        mock_embedding_service: MagicMock, ontology_registry: OntologyProviderRegistry,
    ) -> None:
        """Step 6: Materialize creates stub nodes and wires parent chain."""
        await _ensure_root(db_session)
        bio = await _create_node(
            db_session, "biology-mat-graft",
            parent_id=ALL_CONCEPTS_ID,
        )

        resolved = [
            _ResolvedAncestor(
                entry=AncestorEntry(name="plant energy metabolism"),
                existing_node_id=None,
                needs_creation=True,
            ),
            _ResolvedAncestor(
                entry=AncestorEntry(name="botany-mat-graft"),
                existing_node_id=None,
                needs_creation=True,
            ),
            _ResolvedAncestor(
                entry=AncestorEntry(name="biology-mat-graft"),
                existing_node_id=bio.id,
                needs_creation=False,
            ),
        ]

        pipeline = AncestryPipeline(
            session=db_session,
            model_gateway=mock_model_gateway,
            embedding_service=mock_embedding_service,
            ontology_registry=ontology_registry,
        )

        result = await pipeline._materialize_and_wire(resolved, "concept")

        # Two stub nodes should be created
        assert len(result.nodes_created) == 2

        # Parent should be the most specific stub (plant energy metabolism)
        assert result.parent_id != ALL_CONCEPTS_ID
        assert result.parent_id != bio.id  # not biology — there are stubs below it

        # Verify the chain is wired correctly in the DB
        repo = NodeRepository(db_session)

        # The most specific stub should be the parent
        pem_node = await repo.get_by_id(result.parent_id)
        assert pem_node is not None
        assert pem_node.concept == "plant energy metabolism"
        assert pem_node.metadata_ is not None
        assert pem_node.metadata_.get("stub") is True

        # plant energy metabolism → botany-mat-graft
        botany_node = await repo.get_by_id(pem_node.parent_id)
        assert botany_node is not None
        assert botany_node.concept == "botany-mat-graft"
        assert botany_node.metadata_ is not None
        assert botany_node.metadata_.get("stub") is True

        # botany-mat-graft → biology-mat-graft (existing)
        assert botany_node.parent_id == bio.id

    async def test_step6_materialize_excludes_self_reference(
        self, db_session: AsyncSession, mock_model_gateway: MagicMock,
        mock_embedding_service: MagicMock, ontology_registry: OntologyProviderRegistry,
    ) -> None:
        """Step 6: exclude_node_id filters out self-referential entries."""
        await _ensure_root(db_session)
        # Simulate the node being classified already exists in the DB
        target_node = await _create_node(
            db_session, "algorithms-self",
            parent_id=ALL_CONCEPTS_ID,
        )

        # Chain where one entry's existing_node_id = the target node
        resolved = [
            _ResolvedAncestor(
                entry=AncestorEntry(name="sorting stuff"),
                existing_node_id=None,
                needs_creation=True,
            ),
            _ResolvedAncestor(
                entry=AncestorEntry(name="algorithms-self"),
                existing_node_id=target_node.id,  # matched itself!
                needs_creation=False,
            ),
            _ResolvedAncestor(
                entry=AncestorEntry(name="computer science"),
                existing_node_id=None,
                needs_creation=True,
            ),
        ]

        pipeline = AncestryPipeline(
            session=db_session,
            model_gateway=mock_model_gateway,
            embedding_service=mock_embedding_service,
            ontology_registry=ontology_registry,
        )

        result = await pipeline._materialize_and_wire(
            resolved, "concept", exclude_node_id=target_node.id,
        )

        # The self-referential entry should be filtered out
        # Only "sorting stuff" and "computer science" should be stubs
        assert len(result.nodes_created) == 2
        # Parent should NOT be the target node itself
        assert result.parent_id != target_node.id

    async def test_step6_materialize_no_existing(
        self, db_session: AsyncSession, mock_model_gateway: MagicMock,
        mock_embedding_service: MagicMock, ontology_registry: OntologyProviderRegistry,
    ) -> None:
        """Step 6: All gaps — stubs created with correct wiring to root."""
        await _ensure_root(db_session)

        resolved = [
            _ResolvedAncestor(
                entry=AncestorEntry(name="sorting algorithms-mat"),
                existing_node_id=None,
                needs_creation=True,
            ),
            _ResolvedAncestor(
                entry=AncestorEntry(name="algorithms-mat"),
                existing_node_id=None,
                needs_creation=True,
            ),
        ]

        pipeline = AncestryPipeline(
            session=db_session,
            model_gateway=mock_model_gateway,
            embedding_service=mock_embedding_service,
            ontology_registry=ontology_registry,
        )

        result = await pipeline._materialize_and_wire(resolved, "concept")

        assert len(result.nodes_created) == 2
        assert result.parent_id != ALL_CONCEPTS_ID  # should be "sorting algorithms"

        repo = NodeRepository(db_session)

        # Most specific stub
        sa_node = await repo.get_by_id(result.parent_id)
        assert sa_node is not None
        assert sa_node.concept == "sorting algorithms-mat"

        # Its parent should be "algorithms-mat"
        algo_node = await repo.get_by_id(sa_node.parent_id)
        assert algo_node is not None
        assert algo_node.concept == "algorithms-mat"

        # "algorithms-mat" parent should be the root
        assert algo_node.parent_id == ALL_CONCEPTS_ID


# ── Full pipeline integration tests ─────────────────────────────────────


@pytest.mark.asyncio
class TestAncestryPipelineIntegration:
    async def test_entity_skips_ancestry(
        self, db_session: AsyncSession, mock_model_gateway: MagicMock,
        mock_embedding_service: MagicMock, ontology_registry: OntologyProviderRegistry,
    ) -> None:
        pipeline = AncestryPipeline(
            session=db_session,
            model_gateway=mock_model_gateway,
            embedding_service=mock_embedding_service,
            ontology_registry=ontology_registry,
        )

        result = await pipeline.determine_ancestry("Tesla Inc", "entity")

        assert isinstance(result, AncestryResult)
        assert result.parent_id is None
        assert result.nodes_created == []
        assert result.ancestry_chain == []
        mock_model_gateway.generate.assert_not_awaited()

    async def test_concept_creates_stubs_no_existing(
        self, db_session: AsyncSession, mock_model_gateway: MagicMock,
        mock_embedding_service: MagicMock, ontology_registry: OntologyProviderRegistry,
    ) -> None:
        """AI proposes chain, no existing nodes → stub nodes created and wired."""
        await _ensure_root(db_session)
        mock_model_gateway.generate = AsyncMock(
            return_value=_make_quicksort_chain()
        )

        pipeline = AncestryPipeline(
            session=db_session,
            model_gateway=mock_model_gateway,
            embedding_service=mock_embedding_service,
            ontology_registry=ontology_registry,
        )

        result = await pipeline.determine_ancestry("quicksort", "concept")

        assert isinstance(result, AncestryResult)
        # Stub nodes should have been created
        assert len(result.nodes_created) > 0, "Expected stub nodes but got none"

        # Parent should NOT be the default root — it should be the closest stub
        assert result.parent_id != ALL_CONCEPTS_ID

        # Verify stubs exist in DB and are wired
        repo = NodeRepository(db_session)
        parent = await repo.get_by_id(result.parent_id)
        assert parent is not None
        assert parent.concept == "sorting algorithms"
        assert parent.metadata_ is not None
        assert parent.metadata_.get("stub") is True

        # Walk the chain up to root
        current = parent
        chain_concepts = [current.concept]
        while current.parent_id and current.parent_id != ALL_CONCEPTS_ID:
            current = await repo.get_by_id(current.parent_id)
            assert current is not None
            chain_concepts.append(current.concept)
        chain_concepts.append("all concepts")

        assert "algorithms" in chain_concepts
        assert "computer science" in chain_concepts

    async def test_concept_with_existing_parent(
        self, db_session: AsyncSession, mock_model_gateway: MagicMock,
        mock_embedding_service: MagicMock, ontology_registry: OntologyProviderRegistry,
    ) -> None:
        """When an existing node matches an ancestor, stubs wire through it."""
        await _ensure_root(db_session)

        existing_node = await _create_node(
            db_session, "algorithms",
            parent_id=ALL_CONCEPTS_ID,
        )

        mock_model_gateway.generate = AsyncMock(
            return_value=_make_quicksort_chain()
        )

        pipeline = AncestryPipeline(
            session=db_session,
            model_gateway=mock_model_gateway,
            embedding_service=mock_embedding_service,
            ontology_registry=ontology_registry,
        )

        result = await pipeline.determine_ancestry("quicksort", "concept")

        assert isinstance(result, AncestryResult)
        # "algorithms" exists, "sorting algorithms" is a stub → parent is sorting algorithms
        assert result.parent_id != ALL_CONCEPTS_ID

        # Verify "sorting algorithms" stub exists with algorithms as parent
        repo = NodeRepository(db_session)
        sa_node = await repo.get_by_id(result.parent_id)
        assert sa_node is not None
        assert sa_node.concept == "sorting algorithms"

        # sorting algorithms → algorithms (existing)
        assert sa_node.parent_id == existing_node.id

        # "algorithms" should be in the ancestry chain
        assert existing_node.id in result.ancestry_chain

    async def test_full_with_wikidata(
        self, db_session: AsyncSession, mock_model_gateway: MagicMock,
        mock_embedding_service: MagicMock,
    ) -> None:
        """Full pipeline with both AI and Wikidata chains creates stubs."""
        await _ensure_root(db_session)
        mock_model_gateway.generate = AsyncMock(
            return_value=_make_photosynthesis_chain()
        )

        wikidata_chain = AncestryChain(
            ancestors=[
                AncestorEntry(name="plant physiology"),
                AncestorEntry(name="botany"),
                AncestorEntry(name="biology"),
            ],
            source="wikidata",
        )
        registry = _mock_registry_with_wikidata(wikidata_chain)

        pipeline = AncestryPipeline(
            session=db_session,
            model_gateway=mock_model_gateway,
            embedding_service=mock_embedding_service,
            ontology_registry=registry,
        )

        result = await pipeline.determine_ancestry(
            "photosynthesis", "concept",
            definition="Process of converting light to chemical energy",
        )

        assert isinstance(result, AncestryResult)
        # Stubs should have been created
        assert len(result.nodes_created) > 0

        # Parent should be a stub (not default root)
        assert result.parent_id != ALL_CONCEPTS_ID

    async def test_disabled_feature_falls_back(
        self, db_session: AsyncSession, mock_model_gateway: MagicMock,
        mock_embedding_service: MagicMock, ontology_registry: OntologyProviderRegistry,
    ) -> None:
        """When ontology ancestry is disabled, use default parent."""
        pipeline = AncestryPipeline(
            session=db_session,
            model_gateway=mock_model_gateway,
            embedding_service=mock_embedding_service,
            ontology_registry=ontology_registry,
        )

        with patch("kt_ontology.ancestry.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                enable_ontology_ancestry=False,
                ontology_similarity_threshold=0.85,
            )
            result = await pipeline.determine_ancestry("quicksort", "concept")

        assert result.parent_id == ALL_CONCEPTS_ID
        assert result.nodes_created == []
        mock_model_gateway.generate.assert_not_awaited()

    async def test_circular_parent_prevented(
        self, db_session: AsyncSession, mock_model_gateway: MagicMock,
        mock_embedding_service: MagicMock, ontology_registry: OntologyProviderRegistry,
    ) -> None:
        """Cycle detection: A→B→C→root, then trying to set C.parent=A should be rejected."""
        await _ensure_root(db_session)

        # Create a chain: A → B → C → ALL_CONCEPTS
        node_c = await _create_node(db_session, "node-c-circ", parent_id=ALL_CONCEPTS_ID)
        node_b = await _create_node(db_session, "node-b-circ", parent_id=node_c.id)
        node_a = await _create_node(db_session, "node-a-circ", parent_id=node_b.id)

        from kt_graph.engine import GraphEngine
        engine = GraphEngine(db_session, mock_embedding_service)

        # Setting C.parent = A would create A→B→C→A (cycle, never reaches root)
        with pytest.raises(ValueError, match="would create a cycle"):
            await engine.set_parent(node_c.id, node_a.id)

        # Verify C's parent is unchanged
        repo = NodeRepository(db_session)
        c_refreshed = await repo.get_by_id(node_c.id)
        assert c_refreshed.parent_id == ALL_CONCEPTS_ID

    async def test_orphan_parent_rejected(
        self, db_session: AsyncSession, mock_model_gateway: MagicMock,
        mock_embedding_service: MagicMock, ontology_registry: OntologyProviderRegistry,
    ) -> None:
        """A parent whose chain doesn't reach root should be rejected."""
        await _ensure_root(db_session)

        # Create an orphan node with no parent (not a root node)
        orphan = await _create_node(db_session, "orphan-node", parent_id=None)
        child = await _create_node(db_session, "child-node", parent_id=ALL_CONCEPTS_ID)

        from kt_graph.engine import GraphEngine
        engine = GraphEngine(db_session, mock_embedding_service)

        with pytest.raises(ValueError, match="not a root node"):
            await engine.set_parent(child.id, orphan.id)

    async def test_materialize_skips_cycle_on_reparent(
        self, db_session: AsyncSession, mock_model_gateway: MagicMock,
        mock_embedding_service: MagicMock, ontology_registry: OntologyProviderRegistry,
    ) -> None:
        """_materialize_and_wire should not re-parent if it would create a cycle."""
        await _ensure_root(db_session)

        # Create existing nodes: child → parent → ALL_CONCEPTS
        parent_node = await _create_node(
            db_session, "photosynthesis-rp", parent_id=ALL_CONCEPTS_ID,
        )
        child_node = await _create_node(
            db_session, "chlorophyll-rp", parent_id=parent_node.id,
        )

        # Ancestry pipeline for a NEW node proposes:
        # [new_concept → chlorophyll-rp → photosynthesis-rp]
        # Since chlorophyll-rp.parent = photosynthesis-rp (not default),
        # re-parenting is skipped. But let's test the cycle scenario:
        # If chlorophyll-rp had parent=ALL_CONCEPTS (default), re-parenting
        # it to child_of_child would be safe. But re-parenting photosynthesis-rp
        # to point to chlorophyll-rp would create a cycle.

        # Reset parent_node to default so the re-parent guard kicks in
        await NodeRepository(db_session).update_fields(
            parent_node.id, parent_id=ALL_CONCEPTS_ID,
        )

        resolved = [
            _ResolvedAncestor(
                entry=AncestorEntry(name="chlorophyll-rp"),
                existing_node_id=child_node.id,
                needs_creation=False,
            ),
            _ResolvedAncestor(
                entry=AncestorEntry(name="photosynthesis-rp"),
                existing_node_id=parent_node.id,
                needs_creation=False,
            ),
        ]

        pipeline = AncestryPipeline(
            session=db_session,
            model_gateway=mock_model_gateway,
            embedding_service=mock_embedding_service,
            ontology_registry=ontology_registry,
        )

        result = await pipeline._materialize_and_wire(resolved, "concept")

        # The pipeline should NOT create a cycle
        repo = NodeRepository(db_session)

        # Walk parent chain from child_node — check for loops
        visited: set[uuid.UUID] = set()
        current_id: uuid.UUID | None = child_node.id
        found_cycle = False
        while current_id:
            if current_id in visited:
                found_cycle = True
                break
            visited.add(current_id)
            node = await repo.get_by_id(current_id)
            if node is None or node.parent_id is None:
                break
            current_id = node.parent_id

        assert not found_cycle, (
            f"Cycle detected: visited {visited}, ended at {current_id}"
        )

    async def test_self_referential_parent_prevented(
        self, db_session: AsyncSession, mock_model_gateway: MagicMock,
        mock_embedding_service: MagicMock, ontology_registry: OntologyProviderRegistry,
    ) -> None:
        """When a node matches itself in the ancestry chain, it should be excluded."""
        await _ensure_root(db_session)

        # Create a node that will be classified — its name appears in the LLM chain
        target_node = await _create_node(
            db_session, "algorithms",
            parent_id=ALL_CONCEPTS_ID,
        )

        # LLM proposes a chain that includes "algorithms" as an ancestor
        chain_with_self = json.dumps([
            {"name": "algorithms", "description": "Step-by-step computational procedures"},
            {"name": "computer science", "description": "The study of computation"},
            {"name": "formal science", "description": "Sciences studying formal systems"},
            {"name": "all concepts", "description": "Root"},
        ])
        mock_model_gateway.generate = AsyncMock(return_value=chain_with_self)

        pipeline = AncestryPipeline(
            session=db_session,
            model_gateway=mock_model_gateway,
            embedding_service=mock_embedding_service,
            ontology_registry=ontology_registry,
        )

        # Pass node_id so the pipeline can exclude self-matches
        result = await pipeline.determine_ancestry(
            "algorithms", "concept", node_id=target_node.id,
        )

        # The parent should NOT be the node itself
        assert result.parent_id != target_node.id, (
            f"Self-referential parent detected: node {target_node.id} is its own parent"
        )

    async def test_llm_failure_falls_back(
        self, db_session: AsyncSession, mock_model_gateway: MagicMock,
        mock_embedding_service: MagicMock, ontology_registry: OntologyProviderRegistry,
    ) -> None:
        """When LLM fails, pipeline should fall back to default parent."""
        mock_model_gateway.generate = AsyncMock(side_effect=Exception("LLM down"))

        pipeline = AncestryPipeline(
            session=db_session,
            model_gateway=mock_model_gateway,
            embedding_service=mock_embedding_service,
            ontology_registry=ontology_registry,
        )

        result = await pipeline.determine_ancestry("quicksort", "concept")

        assert isinstance(result, AncestryResult)
        assert result.parent_id == ALL_CONCEPTS_ID
        assert result.nodes_created == []
