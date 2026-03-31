"""Tests for MCP tool functions."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kt_mcp.server import (
    get_dimensions,
    get_edges,
    get_fact_sources,
    get_facts,
    get_node,
    get_node_paths,
    search_facts,
    search_graph,
)


def _make_mock_node(
    concept: str = "quantum computing",
    node_type: str = "concept",
    definition: str | None = "A definition.",
    node_id: uuid.UUID | None = None,
    parent_id: uuid.UUID | None = None,
    metadata: dict | None = None,
) -> MagicMock:
    node = MagicMock()
    node.id = node_id or uuid.uuid4()
    node.concept = concept
    node.node_type = node_type
    node.definition = definition
    node.parent_id = parent_id
    node.created_at = None
    node.metadata_ = metadata
    # Denormalized counters (used by optimized get_node)
    node.fact_count = 0
    node.edge_count = 0
    node.child_count = 0
    node.dimension_count = 0
    node.convergence_score = 0.0
    return node


def _make_mock_dimension(model_id: str = "gpt-4", content: str = "Dim content", confidence: float = 0.9) -> MagicMock:
    dim = MagicMock()
    dim.model_id = model_id
    dim.content = content
    dim.confidence = confidence
    dim.generated_at = None
    return dim


def _make_mock_edge(
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    relationship_type: str = "related",
    weight: float = 0.8,
    justification: str = "Shared concepts",
) -> MagicMock:
    edge = MagicMock()
    edge.id = uuid.uuid4()
    edge.source_node_id = source_id
    edge.target_node_id = target_id
    edge.relationship_type = relationship_type
    edge.weight = weight
    edge.justification = justification
    return edge


def _make_mock_raw_source(
    uri: str = "https://example.com/article",
    title: str | None = "Example Article",
    provider_id: str = "serper",
) -> MagicMock:
    raw = MagicMock()
    raw.id = uuid.uuid4()
    raw.uri = uri
    raw.title = title
    raw.provider_id = provider_id
    raw.retrieved_at = None
    return raw


def _make_mock_fact_source(
    raw_source: MagicMock | None = None,
    author_person: str | None = None,
    author_org: str | None = None,
    attribution: str | None = None,
    context_snippet: str | None = None,
) -> MagicMock:
    fs = MagicMock()
    fs.raw_source = raw_source or _make_mock_raw_source()
    fs.author_person = author_person
    fs.author_org = author_org
    fs.attribution = attribution
    fs.context_snippet = context_snippet
    return fs


def _make_mock_fact(
    content: str = "A fact",
    fact_type: str = "claim",
    sources: list[MagicMock] | None = None,
) -> MagicMock:
    fact = MagicMock()
    fact.id = uuid.uuid4()
    fact.content = content
    fact.fact_type = fact_type
    fact.created_at = None
    fact.sources = sources if sources is not None else []
    return fact


def _mock_session_context():
    """Return a mock that works as `async with factory() as session:`."""
    mock_session = AsyncMock()
    mock_execute_result = MagicMock()
    mock_execute_result.all.return_value = []
    mock_execute_result.scalar.return_value = 0
    mock_session.execute = AsyncMock(return_value=mock_execute_result)

    mock_factory = MagicMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory.return_value = mock_ctx

    return mock_factory, mock_session


class TestSearchGraph:
    @pytest.mark.asyncio
    async def test_search_returns_nodes(self):
        factory, session = _mock_session_context()
        node = _make_mock_node()

        with (
            patch("kt_mcp.server.get_session_factory_cached", return_value=factory),
            patch("kt_mcp.server.get_qdrant_client_cached", return_value=MagicMock()),
            patch("kt_mcp.server.GraphEngine") as MockEngine,
        ):
            engine_instance = MockEngine.return_value
            engine_instance.search_nodes = AsyncMock(return_value=[node])

            result = await search_graph("quantum", limit=10)

        assert result["total"] == 1
        assert result["nodes"][0]["concept"] == "quantum computing"
        assert result["nodes"][0]["node_type"] == "concept"
        assert "fact_count" in result["nodes"][0]
        # Lightweight: no definition, parent_id, or edge_count
        assert "definition" not in result["nodes"][0]
        assert "parent_id" not in result["nodes"][0]
        assert "edge_count" not in result["nodes"][0]

    @pytest.mark.asyncio
    async def test_search_empty_result(self):
        factory, session = _mock_session_context()

        with (
            patch("kt_mcp.server.get_session_factory_cached", return_value=factory),
            patch("kt_mcp.server.get_qdrant_client_cached", return_value=MagicMock()),
            patch("kt_mcp.server.GraphEngine") as MockEngine,
        ):
            engine_instance = MockEngine.return_value
            engine_instance.search_nodes = AsyncMock(return_value=[])

            result = await search_graph("nonexistent")

        assert result["nodes"] == []
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_search_clamps_limit(self):
        factory, session = _mock_session_context()

        with (
            patch("kt_mcp.server.get_session_factory_cached", return_value=factory),
            patch("kt_mcp.server.get_qdrant_client_cached", return_value=MagicMock()),
            patch("kt_mcp.server.GraphEngine") as MockEngine,
        ):
            engine_instance = MockEngine.return_value
            engine_instance.search_nodes = AsyncMock(return_value=[])

            await search_graph("test", limit=999)
            engine_instance.search_nodes.assert_called_once_with("test", limit=100, node_type=None)


class TestGetNode:
    @pytest.mark.asyncio
    async def test_get_node_with_definition(self):
        """Node with a definition returns core info + counts, no fallback dimension."""
        factory, session = _mock_session_context()
        node_id = uuid.uuid4()
        node = _make_mock_node(node_id=node_id, definition="A real definition.")

        with (
            patch("kt_mcp.server.get_session_factory_cached", return_value=factory),
            patch("kt_mcp.server.get_qdrant_client_cached", return_value=MagicMock()),
            patch("kt_mcp.server.GraphEngine") as MockEngine,
        ):
            engine_instance = MockEngine.return_value
            engine_instance.get_node = AsyncMock(return_value=node)

            result = await get_node(str(node_id))

        assert result["concept"] == "quantum computing"
        assert result["definition"] == "A real definition."
        assert "fact_count" in result
        assert "edge_count" in result
        assert "dimension_count" in result
        assert "fallback_dimension" not in result
        # No edges or dimensions arrays
        assert "edges" not in result
        assert "dimensions" not in result

    @pytest.mark.asyncio
    async def test_get_node_no_definition_has_fallback_dimension(self):
        """Node without definition includes one fallback dimension."""
        factory, session = _mock_session_context()
        node_id = uuid.uuid4()
        node = _make_mock_node(node_id=node_id, definition=None)
        node.dimension_count = 1  # Trigger fallback logic
        dim = _make_mock_dimension(content="Fallback content")

        with (
            patch("kt_mcp.server.get_session_factory_cached", return_value=factory),
            patch("kt_mcp.server.get_qdrant_client_cached", return_value=MagicMock()),
            patch("kt_mcp.server.GraphEngine") as MockEngine,
        ):
            engine_instance = MockEngine.return_value
            engine_instance.get_node = AsyncMock(return_value=node)
            engine_instance.get_dimensions = AsyncMock(return_value=[dim])

            result = await get_node(str(node_id))

        assert result["definition"] is None
        assert "fallback_dimension" in result
        assert result["fallback_dimension"]["content"] == "Fallback content"
        assert result["fallback_dimension"]["model_id"] == "gpt-4"

    @pytest.mark.asyncio
    async def test_get_node_no_definition_no_dimensions(self):
        """Node without definition and no dimensions has no fallback."""
        factory, session = _mock_session_context()
        node_id = uuid.uuid4()
        node = _make_mock_node(node_id=node_id, definition=None)

        with (
            patch("kt_mcp.server.get_session_factory_cached", return_value=factory),
            patch("kt_mcp.server.get_qdrant_client_cached", return_value=MagicMock()),
            patch("kt_mcp.server.GraphEngine") as MockEngine,
        ):
            engine_instance = MockEngine.return_value
            engine_instance.get_node = AsyncMock(return_value=node)

            result = await get_node(str(node_id))

        assert result["definition"] is None
        assert "fallback_dimension" not in result

    @pytest.mark.asyncio
    async def test_get_node_invalid_id(self):
        result = await get_node("not-a-uuid")
        assert result["error"] == "Invalid node ID format"

    @pytest.mark.asyncio
    async def test_get_node_not_found(self):
        factory, session = _mock_session_context()

        with (
            patch("kt_mcp.server.get_session_factory_cached", return_value=factory),
            patch("kt_mcp.server.get_qdrant_client_cached", return_value=MagicMock()),
            patch("kt_mcp.server.GraphEngine") as MockEngine,
        ):
            engine_instance = MockEngine.return_value
            engine_instance.get_node = AsyncMock(return_value=None)

            result = await get_node(str(uuid.uuid4()))

        assert result["error"] == "Node not found"


class TestGetDimensions:
    @pytest.mark.asyncio
    async def test_get_dimensions_returns_paginated(self):
        factory, session = _mock_session_context()
        node = _make_mock_node()
        dims = [_make_mock_dimension(model_id=f"model-{i}") for i in range(5)]

        with (
            patch("kt_mcp.server.get_session_factory_cached", return_value=factory),
            patch("kt_mcp.server.get_qdrant_client_cached", return_value=MagicMock()),
            patch("kt_mcp.server.GraphEngine") as MockEngine,
        ):
            engine_instance = MockEngine.return_value
            engine_instance.get_node = AsyncMock(return_value=node)
            engine_instance.get_dimensions = AsyncMock(return_value=dims)

            result = await get_dimensions(str(node.id), limit=3, offset=0)

        assert result["total"] == 5
        assert result["returned"] == 3
        assert result["offset"] == 0
        assert len(result["dimensions"]) == 3
        assert result["dimensions"][0]["model_id"] == "model-0"

    @pytest.mark.asyncio
    async def test_get_dimensions_with_offset(self):
        factory, session = _mock_session_context()
        node = _make_mock_node()
        dims = [_make_mock_dimension(model_id=f"model-{i}") for i in range(5)]

        with (
            patch("kt_mcp.server.get_session_factory_cached", return_value=factory),
            patch("kt_mcp.server.get_qdrant_client_cached", return_value=MagicMock()),
            patch("kt_mcp.server.GraphEngine") as MockEngine,
        ):
            engine_instance = MockEngine.return_value
            engine_instance.get_node = AsyncMock(return_value=node)
            engine_instance.get_dimensions = AsyncMock(return_value=dims)

            result = await get_dimensions(str(node.id), limit=10, offset=3)

        assert result["total"] == 5
        assert result["returned"] == 2
        assert result["offset"] == 3
        assert result["dimensions"][0]["model_id"] == "model-3"

    @pytest.mark.asyncio
    async def test_get_dimensions_invalid_id(self):
        result = await get_dimensions("bad")
        assert result["error"] == "Invalid node ID format"

    @pytest.mark.asyncio
    async def test_get_dimensions_not_found(self):
        factory, session = _mock_session_context()

        with (
            patch("kt_mcp.server.get_session_factory_cached", return_value=factory),
            patch("kt_mcp.server.get_qdrant_client_cached", return_value=MagicMock()),
            patch("kt_mcp.server.GraphEngine") as MockEngine,
        ):
            engine_instance = MockEngine.return_value
            engine_instance.get_node = AsyncMock(return_value=None)

            result = await get_dimensions(str(uuid.uuid4()))

        assert result["error"] == "Node not found"


class TestGetEdges:
    @pytest.mark.asyncio
    async def test_get_edges_returns_paginated(self):
        factory, session = _mock_session_context()
        node_id = uuid.uuid4()
        node = _make_mock_node(node_id=node_id)
        other_ids = [uuid.uuid4() for _ in range(5)]
        edges = [_make_mock_edge(node_id, oid) for oid in other_ids]

        # Mock target node batch query
        mock_rows = []
        for oid in other_ids[:3]:  # Only first page
            row = MagicMock()
            row.id = oid
            row.concept = f"concept-{oid}"
            row.node_type = "concept"
            mock_rows.append(row)

        mock_result = MagicMock()
        mock_result.all.return_value = mock_rows

        # Mock edge fact count query (returns empty — no facts)
        mock_fact_result = MagicMock()
        mock_fact_result.all.return_value = []

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            # First call is edge fact counts, second is target node batch
            if call_count == 1:
                return mock_fact_result
            return mock_result

        with (
            patch("kt_mcp.server.get_session_factory_cached", return_value=factory),
            patch("kt_mcp.server.get_qdrant_client_cached", return_value=MagicMock()),
            patch("kt_mcp.server.GraphEngine") as MockEngine,
        ):
            engine_instance = MockEngine.return_value
            engine_instance.get_node = AsyncMock(return_value=node)
            engine_instance.get_edges = AsyncMock(return_value=edges)
            session.execute = AsyncMock(side_effect=mock_execute)

            result = await get_edges(str(node_id), limit=3, offset=0)

        assert result["total"] == 5
        assert result["returned"] == 3
        assert result["offset"] == 0
        assert "fact_count" in result["edges"][0]

    @pytest.mark.asyncio
    async def test_get_edges_sorted_by_fact_count(self):
        """Edges are sorted by fact count descending."""
        factory, session = _mock_session_context()
        node_id = uuid.uuid4()
        node = _make_mock_node(node_id=node_id)
        other_ids = [uuid.uuid4() for _ in range(3)]
        edges = [_make_mock_edge(node_id, oid) for oid in other_ids]

        # Assign fact counts: edge[0]=1, edge[1]=5, edge[2]=3
        fact_count_map = {edges[0].id: 1, edges[1].id: 5, edges[2].id: 3}
        fact_rows = [(eid, cnt) for eid, cnt in fact_count_map.items()]

        mock_fact_result = MagicMock()
        mock_fact_result.all.return_value = fact_rows

        mock_node_rows = []
        for oid in other_ids:
            row = MagicMock()
            row.id = oid
            row.concept = f"concept-{oid}"
            row.node_type = "concept"
            mock_node_rows.append(row)

        mock_node_result = MagicMock()
        mock_node_result.all.return_value = mock_node_rows

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_fact_result
            return mock_node_result

        with (
            patch("kt_mcp.server.get_session_factory_cached", return_value=factory),
            patch("kt_mcp.server.get_qdrant_client_cached", return_value=MagicMock()),
            patch("kt_mcp.server.GraphEngine") as MockEngine,
        ):
            engine_instance = MockEngine.return_value
            engine_instance.get_node = AsyncMock(return_value=node)
            engine_instance.get_edges = AsyncMock(return_value=edges)
            session.execute = AsyncMock(side_effect=mock_execute)

            result = await get_edges(str(node_id), limit=10)

        # Should be sorted: 5, 3, 1
        assert result["edges"][0]["fact_count"] == 5
        assert result["edges"][1]["fact_count"] == 3
        assert result["edges"][2]["fact_count"] == 1

    @pytest.mark.asyncio
    async def test_get_edges_filter_by_type(self):
        factory, session = _mock_session_context()
        node_id = uuid.uuid4()
        node = _make_mock_node(node_id=node_id)
        related_edge = _make_mock_edge(node_id, uuid.uuid4(), relationship_type="related")
        cross_edge = _make_mock_edge(node_id, uuid.uuid4(), relationship_type="cross_type")

        with (
            patch("kt_mcp.server.get_session_factory_cached", return_value=factory),
            patch("kt_mcp.server.get_qdrant_client_cached", return_value=MagicMock()),
            patch("kt_mcp.server.GraphEngine") as MockEngine,
        ):
            engine_instance = MockEngine.return_value
            engine_instance.get_node = AsyncMock(return_value=node)
            engine_instance.get_edges = AsyncMock(return_value=[related_edge, cross_edge])

            result = await get_edges(str(node_id), edge_type="related")

        assert result["total"] == 1
        assert result["edges"][0]["relationship_type"] == "related"

    @pytest.mark.asyncio
    async def test_get_edges_invalid_id(self):
        result = await get_edges("bad")
        assert result["error"] == "Invalid node ID format"

    @pytest.mark.asyncio
    async def test_get_edges_not_found(self):
        factory, session = _mock_session_context()

        with (
            patch("kt_mcp.server.get_session_factory_cached", return_value=factory),
            patch("kt_mcp.server.get_qdrant_client_cached", return_value=MagicMock()),
            patch("kt_mcp.server.GraphEngine") as MockEngine,
        ):
            engine_instance = MockEngine.return_value
            engine_instance.get_node = AsyncMock(return_value=None)

            result = await get_edges(str(uuid.uuid4()))

        assert result["error"] == "Node not found"


class TestGetFacts:
    @pytest.mark.asyncio
    async def test_get_facts_grouped_by_source(self):
        """Facts are grouped by their primary source with author info."""
        factory, session = _mock_session_context()
        node = _make_mock_node()

        raw_src = _make_mock_raw_source(uri="https://example.com/article", title="Article")
        fs = _make_mock_fact_source(
            raw_source=raw_src,
            author_person="Jane Doe",
            author_org="ACME Corp",
        )
        fact = _make_mock_fact(sources=[fs])

        with (
            patch("kt_mcp.server.get_session_factory_cached", return_value=factory),
            patch("kt_mcp.server.get_qdrant_client_cached", return_value=MagicMock()),
            patch("kt_mcp.server.GraphEngine") as MockEngine,
        ):
            engine_instance = MockEngine.return_value
            engine_instance.get_node = AsyncMock(return_value=node)
            engine_instance.get_node_facts_with_sources = AsyncMock(return_value=[fact])

            result = await get_facts(str(node.id))

        assert result["returned_facts"] == 1
        assert result["total_facts"] == 1
        assert result["total_sources"] == 1
        assert result["offset"] == 0
        assert result["next_offset"] is None  # No more pages
        group = result["source_groups"][0]
        assert group["uri"] == "https://example.com/article"
        assert group["title"] == "Article"
        assert group["author_person"] == "Jane Doe"
        assert group["author_org"] == "ACME Corp"
        assert group["facts"][0]["content"] == "A fact"

    @pytest.mark.asyncio
    async def test_get_facts_multiple_sources(self):
        """Facts from different sources produce separate groups sorted by count."""
        factory, session = _mock_session_context()
        node = _make_mock_node()

        src_a = _make_mock_raw_source(uri="https://a.com")
        src_b = _make_mock_raw_source(uri="https://b.com")

        # 3 facts from source A, 1 from source B
        facts = [
            _make_mock_fact(content=f"Fact A{i}", sources=[_make_mock_fact_source(raw_source=src_a)]) for i in range(3)
        ] + [
            _make_mock_fact(content="Fact B0", sources=[_make_mock_fact_source(raw_source=src_b)]),
        ]

        with (
            patch("kt_mcp.server.get_session_factory_cached", return_value=factory),
            patch("kt_mcp.server.get_qdrant_client_cached", return_value=MagicMock()),
            patch("kt_mcp.server.GraphEngine") as MockEngine,
        ):
            engine_instance = MockEngine.return_value
            engine_instance.get_node = AsyncMock(return_value=node)
            engine_instance.get_node_facts_with_sources = AsyncMock(return_value=facts)

            result = await get_facts(str(node.id))

        assert result["total_sources"] == 2
        assert result["returned_facts"] == 4
        assert result["next_offset"] is None
        # Source A has more facts, should be first
        assert result["source_groups"][0]["uri"] == "https://a.com"
        assert len(result["source_groups"][0]["facts"]) == 3
        assert result["source_groups"][1]["uri"] == "https://b.com"
        assert len(result["source_groups"][1]["facts"]) == 1

    @pytest.mark.asyncio
    async def test_get_facts_respects_limit(self):
        factory, session = _mock_session_context()
        node = _make_mock_node()
        raw_src = _make_mock_raw_source()
        facts = [
            _make_mock_fact(
                content=f"Fact {i}",
                sources=[_make_mock_fact_source(raw_source=raw_src)],
            )
            for i in range(10)
        ]

        with (
            patch("kt_mcp.server.get_session_factory_cached", return_value=factory),
            patch("kt_mcp.server.get_qdrant_client_cached", return_value=MagicMock()),
            patch("kt_mcp.server.GraphEngine") as MockEngine,
        ):
            engine_instance = MockEngine.return_value
            engine_instance.get_node = AsyncMock(return_value=node)
            engine_instance.get_node_facts_with_sources = AsyncMock(return_value=facts)

            result = await get_facts(str(node.id), limit=3)

        assert result["returned_facts"] == 3
        assert result["total_facts"] == 10
        assert result["offset"] == 0
        assert result["next_offset"] == 3  # More pages available

    @pytest.mark.asyncio
    async def test_get_facts_pagination(self):
        """Offset/limit lets the AI page through facts."""
        factory, session = _mock_session_context()
        node = _make_mock_node()
        raw_src = _make_mock_raw_source()
        facts = [
            _make_mock_fact(
                content=f"Fact {i}",
                sources=[_make_mock_fact_source(raw_source=raw_src)],
            )
            for i in range(5)
        ]

        with (
            patch("kt_mcp.server.get_session_factory_cached", return_value=factory),
            patch("kt_mcp.server.get_qdrant_client_cached", return_value=MagicMock()),
            patch("kt_mcp.server.GraphEngine") as MockEngine,
        ):
            engine_instance = MockEngine.return_value
            engine_instance.get_node = AsyncMock(return_value=node)
            engine_instance.get_node_facts_with_sources = AsyncMock(return_value=facts)

            # First page
            result1 = await get_facts(str(node.id), limit=3, offset=0)

        assert result1["returned_facts"] == 3
        assert result1["next_offset"] == 3

        with (
            patch("kt_mcp.server.get_session_factory_cached", return_value=factory),
            patch("kt_mcp.server.get_qdrant_client_cached", return_value=MagicMock()),
            patch("kt_mcp.server.GraphEngine") as MockEngine,
        ):
            engine_instance = MockEngine.return_value
            engine_instance.get_node = AsyncMock(return_value=node)
            engine_instance.get_node_facts_with_sources = AsyncMock(return_value=facts)

            # Second page
            result2 = await get_facts(str(node.id), limit=3, offset=3)

        assert result2["returned_facts"] == 2
        assert result2["next_offset"] is None  # Last page

    @pytest.mark.asyncio
    async def test_get_facts_no_sources(self):
        """Facts without sources are grouped under a null source."""
        factory, session = _mock_session_context()
        node = _make_mock_node()
        fact = _make_mock_fact(sources=[])

        with (
            patch("kt_mcp.server.get_session_factory_cached", return_value=factory),
            patch("kt_mcp.server.get_qdrant_client_cached", return_value=MagicMock()),
            patch("kt_mcp.server.GraphEngine") as MockEngine,
        ):
            engine_instance = MockEngine.return_value
            engine_instance.get_node = AsyncMock(return_value=node)
            engine_instance.get_node_facts_with_sources = AsyncMock(return_value=[fact])

            result = await get_facts(str(node.id))

        assert result["returned_facts"] == 1
        group = result["source_groups"][0]
        assert group["source_id"] is None
        assert group["uri"] is None

    @pytest.mark.asyncio
    async def test_get_facts_invalid_id(self):
        result = await get_facts("bad-id")
        assert result["error"] == "Invalid node ID format"


class TestGetFactSources:
    @pytest.mark.asyncio
    async def test_get_fact_sources_deduplicates(self):
        factory, session = _mock_session_context()
        node = _make_mock_node()

        raw_source = _make_mock_raw_source()

        fs1 = _make_mock_fact_source(
            raw_source=raw_source,
            context_snippet="snippet 1",
            author_person="Alice",
            author_org="Org A",
            attribution="Alice, Org A",
        )
        fs2 = _make_mock_fact_source(
            raw_source=raw_source,  # Same source
            context_snippet="snippet 2",
        )

        fact1 = _make_mock_fact(sources=[fs1])
        fact2 = _make_mock_fact(sources=[fs2])

        with (
            patch("kt_mcp.server.get_session_factory_cached", return_value=factory),
            patch("kt_mcp.server.get_qdrant_client_cached", return_value=MagicMock()),
            patch("kt_mcp.server.GraphEngine") as MockEngine,
        ):
            engine_instance = MockEngine.return_value
            engine_instance.get_node = AsyncMock(return_value=node)
            engine_instance.get_node_facts_with_sources = AsyncMock(return_value=[fact1, fact2])

            result = await get_fact_sources(str(node.id))

        assert result["total_facts"] == 2
        assert result["total_unique_sources"] == 1
        src = result["sources"][0]
        assert src["uri"] == "https://example.com/article"
        assert src["author_person"] == "Alice"
        assert src["author_org"] == "Org A"
        assert src["attribution"] == "Alice, Org A"

    @pytest.mark.asyncio
    async def test_get_fact_sources_invalid_id(self):
        result = await get_fact_sources("bad")
        assert result["error"] == "Invalid node ID format"


class TestSearchFacts:
    @pytest.mark.asyncio
    async def test_search_returns_facts_with_sources_and_nodes(self):
        """Search returns facts with sources and linked nodes."""
        factory, session = _mock_session_context()
        raw_src = _make_mock_raw_source(uri="https://source.com")
        fs = _make_mock_fact_source(
            raw_source=raw_src,
            author_person="Bob",
            author_org="Research Inc",
        )
        fact = _make_mock_fact(content="Quantum entanglement occurs", sources=[fs])

        node_id = uuid.uuid4()
        node_link_row = MagicMock()
        node_link_row.__getitem__ = lambda self, i: [fact.id, node_id, "quantum computing", "concept"][i]

        # Mock execute calls: source query, then node link query
        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            mock_result = MagicMock()
            if call_count == 1:
                # Source query — return fact with sources
                mock_result.scalars.return_value.all.return_value = [fact]
            elif call_count == 2:
                # Node link query
                mock_result.all.return_value = [
                    (fact.id, node_id, "quantum computing", "concept"),
                ]
            else:
                mock_result.all.return_value = []
                mock_result.scalars.return_value.all.return_value = []
            return mock_result

        with (
            patch("kt_mcp.server.get_session_factory_cached", return_value=factory),
            patch("kt_mcp.server.get_qdrant_client_cached", return_value=MagicMock()),
            patch("kt_mcp.server.GraphEngine") as MockEngine,
        ):
            engine_instance = MockEngine.return_value
            engine_instance.count_facts = AsyncMock(return_value=1)
            engine_instance.list_facts = AsyncMock(return_value=[fact])
            session.execute = AsyncMock(side_effect=mock_execute)

            result = await search_facts("quantum")

        assert result["returned"] == 1
        assert result["total"] == 1
        assert result["offset"] == 0
        assert result["next_offset"] is None
        item = result["facts"][0]
        assert item["content"] == "Quantum entanglement occurs"
        assert item["sources"][0]["uri"] == "https://source.com"
        assert item["sources"][0]["author_person"] == "Bob"
        assert item["linked_nodes"][0]["concept"] == "quantum computing"

    @pytest.mark.asyncio
    async def test_search_pagination(self):
        """Offset/limit pagination works for fact search."""
        factory, session = _mock_session_context()
        facts = [_make_mock_fact(content=f"Fact {i}") for i in range(5)]

        async def mock_execute(stmt):
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = []
            mock_result.all.return_value = []
            return mock_result

        with (
            patch("kt_mcp.server.get_session_factory_cached", return_value=factory),
            patch("kt_mcp.server.get_qdrant_client_cached", return_value=MagicMock()),
            patch("kt_mcp.server.GraphEngine") as MockEngine,
        ):
            engine_instance = MockEngine.return_value
            engine_instance.count_facts = AsyncMock(return_value=5)
            engine_instance.list_facts = AsyncMock(return_value=facts[:3])
            session.execute = AsyncMock(side_effect=mock_execute)

            result = await search_facts("test", limit=3, offset=0)

        assert result["returned"] == 3
        assert result["total"] == 5
        assert result["next_offset"] == 3

    @pytest.mark.asyncio
    async def test_search_empty_result(self):
        factory, session = _mock_session_context()

        with (
            patch("kt_mcp.server.get_session_factory_cached", return_value=factory),
            patch("kt_mcp.server.get_qdrant_client_cached", return_value=MagicMock()),
            patch("kt_mcp.server.GraphEngine") as MockEngine,
        ):
            engine_instance = MockEngine.return_value
            engine_instance.count_facts = AsyncMock(return_value=0)
            engine_instance.list_facts = AsyncMock(return_value=[])

            result = await search_facts("nonexistent")

        assert result["facts"] == []
        assert result["total"] == 0
        assert result["next_offset"] is None

    @pytest.mark.asyncio
    async def test_search_clamps_limit(self):
        factory, session = _mock_session_context()

        with (
            patch("kt_mcp.server.get_session_factory_cached", return_value=factory),
            patch("kt_mcp.server.get_qdrant_client_cached", return_value=MagicMock()),
            patch("kt_mcp.server.GraphEngine") as MockEngine,
        ):
            engine_instance = MockEngine.return_value
            engine_instance.count_facts = AsyncMock(return_value=0)
            engine_instance.list_facts = AsyncMock(return_value=[])

            await search_facts("test", limit=999)

            engine_instance.list_facts.assert_called_once_with(
                offset=0,
                limit=100,
                search="test",
                fact_type=None,
                author_org=None,
                source_domain=None,
            )


def _make_mock_path_step(node_id: uuid.UUID, edge: MagicMock | None = None) -> MagicMock:
    step = MagicMock()
    step.node_id = node_id
    step.edge = edge
    return step


class TestGetNodePaths:
    @pytest.mark.asyncio
    async def test_returns_paths_between_nodes(self):
        """Shortest path with edge details between two nodes."""
        factory, session = _mock_session_context()

        src_id = uuid.uuid4()
        mid_id = uuid.uuid4()
        tgt_id = uuid.uuid4()

        src_node = _make_mock_node(node_id=src_id, concept="quantum computing")
        mid_node = _make_mock_node(node_id=mid_id, concept="cryptography")
        tgt_node = _make_mock_node(node_id=tgt_id, concept="information theory")

        edge1 = _make_mock_edge(src_id, mid_id, relationship_type="related", weight=0.8)
        edge2 = _make_mock_edge(mid_id, tgt_id, relationship_type="related", weight=0.6)

        path = [
            _make_mock_path_step(src_id, edge=None),
            _make_mock_path_step(mid_id, edge=edge1),
            _make_mock_path_step(tgt_id, edge=edge2),
        ]

        with (
            patch("kt_mcp.server.get_session_factory_cached", return_value=factory),
            patch("kt_mcp.server.get_qdrant_client_cached", return_value=MagicMock()),
            patch("kt_mcp.server.GraphEngine") as MockEngine,
        ):
            engine_instance = MockEngine.return_value
            engine_instance.get_node = AsyncMock(
                side_effect=lambda uid: {src_id: src_node, tgt_id: tgt_node}.get(uid),
            )
            engine_instance.find_shortest_paths = AsyncMock(return_value=[path])
            engine_instance.get_nodes_by_ids = AsyncMock(
                return_value=[src_node, mid_node, tgt_node],
            )

            result = await get_node_paths(str(src_id), str(tgt_id))

        assert result["total_found"] == 1
        assert result["source"]["concept"] == "quantum computing"
        assert result["target"]["concept"] == "information theory"
        p = result["paths"][0]
        assert p["length"] == 2
        assert len(p["steps"]) == 3
        # First step has no edge
        assert "edge" not in p["steps"][0]
        assert p["steps"][0]["concept"] == "quantum computing"
        # Second step has edge
        assert p["steps"][1]["edge"]["relationship_type"] == "related"
        assert p["steps"][1]["concept"] == "cryptography"
        # Third step has edge
        assert p["steps"][2]["concept"] == "information theory"

    @pytest.mark.asyncio
    async def test_no_path_found(self):
        """Returns empty paths list with message when no path exists."""
        factory, session = _mock_session_context()

        src_id = uuid.uuid4()
        tgt_id = uuid.uuid4()
        src_node = _make_mock_node(node_id=src_id, concept="A")
        tgt_node = _make_mock_node(node_id=tgt_id, concept="B")

        with (
            patch("kt_mcp.server.get_session_factory_cached", return_value=factory),
            patch("kt_mcp.server.get_qdrant_client_cached", return_value=MagicMock()),
            patch("kt_mcp.server.GraphEngine") as MockEngine,
        ):
            engine_instance = MockEngine.return_value
            engine_instance.get_node = AsyncMock(
                side_effect=lambda uid: {src_id: src_node, tgt_id: tgt_node}.get(uid),
            )
            engine_instance.find_shortest_paths = AsyncMock(return_value=[])

            result = await get_node_paths(str(src_id), str(tgt_id))

        assert result["paths"] == []
        assert result["total_found"] == 0
        assert "message" in result

    @pytest.mark.asyncio
    async def test_source_not_found(self):
        factory, session = _mock_session_context()

        with (
            patch("kt_mcp.server.get_session_factory_cached", return_value=factory),
            patch("kt_mcp.server.get_qdrant_client_cached", return_value=MagicMock()),
            patch("kt_mcp.server.GraphEngine") as MockEngine,
        ):
            engine_instance = MockEngine.return_value
            engine_instance.get_node = AsyncMock(return_value=None)

            result = await get_node_paths(str(uuid.uuid4()), str(uuid.uuid4()))

        assert result["error"] == "Source node not found"

    @pytest.mark.asyncio
    async def test_target_not_found(self):
        factory, session = _mock_session_context()
        src_node = _make_mock_node()

        with (
            patch("kt_mcp.server.get_session_factory_cached", return_value=factory),
            patch("kt_mcp.server.get_qdrant_client_cached", return_value=MagicMock()),
            patch("kt_mcp.server.GraphEngine") as MockEngine,
        ):
            engine_instance = MockEngine.return_value
            # First call returns source, second returns None (target)
            engine_instance.get_node = AsyncMock(side_effect=[src_node, None])

            result = await get_node_paths(str(src_node.id), str(uuid.uuid4()))

        assert result["error"] == "Target node not found"

    @pytest.mark.asyncio
    async def test_invalid_source_id(self):
        result = await get_node_paths("bad", str(uuid.uuid4()))
        assert result["error"] == "Invalid source node ID format"

    @pytest.mark.asyncio
    async def test_invalid_target_id(self):
        result = await get_node_paths(str(uuid.uuid4()), "bad")
        assert result["error"] == "Invalid target node ID format"
