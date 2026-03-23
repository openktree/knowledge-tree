"""Tests for the Query Agent — state, tools, and agent behavior."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from kt_worker_query.agents.query_agent_state import QueryAgentState
from kt_agents_core.state import AgentContext
from kt_worker_query.agents.tools.query_tools import (
    create_query_tools,
    lightweight_get_node_facts,
    lightweight_read_node,
    lightweight_search_nodes,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mock_node(
    node_id: uuid.UUID | None = None,
    concept: str = "test concept",
    node_type: str = "concept",
    parent_id: uuid.UUID | None = None,
) -> MagicMock:
    node = MagicMock()
    node.id = node_id or uuid.uuid4()
    node.concept = concept
    node.node_type = node_type
    node.parent_id = parent_id
    node.access_count = 5
    node.update_count = 1
    node.attractor = None
    node.filter_id = None
    node.max_content_tokens = 500
    node.created_at = MagicMock(isoformat=MagicMock(return_value="2026-01-01T00:00:00"))
    node.updated_at = MagicMock(isoformat=MagicMock(return_value="2026-01-01T00:00:00"))
    return node


def _make_mock_fact(
    fact_id: uuid.UUID | None = None,
    content: str = "test fact content",
    fact_type: str = "claim",
) -> MagicMock:
    fact = MagicMock()
    fact.id = fact_id or uuid.uuid4()
    fact.content = content
    fact.fact_type = fact_type
    return fact


def _make_mock_edge(
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    rel_type: str = "related",
    weight: float = 0.7,
) -> MagicMock:
    edge = MagicMock()
    edge.id = uuid.uuid4()
    edge.source_node_id = source_id
    edge.target_node_id = target_id
    edge.relationship_type = rel_type
    edge.weight = weight
    edge.created_at = MagicMock(isoformat=MagicMock(return_value="2026-01-01T00:00:00"))
    return edge


def _make_mock_dimension(model_id: str = "claude-3.5") -> MagicMock:
    dim = MagicMock()
    dim.model_id = model_id
    dim.content = "A test dimension content"
    dim.confidence = 0.85
    dim.suggested_concepts = ["related topic"]
    return dim


def _make_ctx() -> AgentContext:
    graph_engine = MagicMock()
    graph_engine.search_nodes = AsyncMock(return_value=[])
    graph_engine.find_similar_nodes = AsyncMock(return_value=[])
    graph_engine.get_node = AsyncMock(return_value=None)
    graph_engine.get_dimensions = AsyncMock(return_value=[])
    graph_engine.get_edges = AsyncMock(return_value=[])
    graph_engine.get_node_facts = AsyncMock(return_value=[])
    graph_engine.compute_richness = MagicMock(return_value=0.5)
    graph_engine.is_node_stale = MagicMock(return_value=False)
    graph_engine.get_perspective_summary = AsyncMock(
        return_value={"supporting": 3, "challenging": 1, "neutral": 0, "unclassified": 0}
    )
    graph_engine.increment_access_count = AsyncMock()

    provider_registry = MagicMock()
    model_gateway = MagicMock()
    embedding_service = MagicMock()
    embedding_service.embed_batch = AsyncMock(return_value=[])
    session = AsyncMock()

    return AgentContext(
        graph_engine=graph_engine,
        provider_registry=provider_registry,
        model_gateway=model_gateway,
        embedding_service=embedding_service,
        session=session,
        emit_event=AsyncMock(),
    )


def _make_state(nav_budget: int = 50) -> QueryAgentState:
    return QueryAgentState(query="test query", nav_budget=nav_budget)


# ---------------------------------------------------------------------------
# QueryAgentState tests
# ---------------------------------------------------------------------------


class TestQueryAgentState:
    def test_nav_remaining(self) -> None:
        state = QueryAgentState(query="test", nav_budget=10, nav_used=3)
        assert state.nav_remaining == 7

    def test_nav_remaining_zero(self) -> None:
        state = QueryAgentState(query="test", nav_budget=5, nav_used=5)
        assert state.nav_remaining == 0

    def test_nav_remaining_clamped(self) -> None:
        state = QueryAgentState(query="test", nav_budget=5, nav_used=8)
        assert state.nav_remaining == 0

    def test_has_visited_this_turn(self) -> None:
        state = QueryAgentState(
            query="test", nav_budget=10, visited_nodes=["abc"]
        )
        assert state.has_visited("abc") is True
        assert state.has_visited("xyz") is False

    def test_has_visited_prior_turn(self) -> None:
        state = QueryAgentState(
            query="test", nav_budget=10, prior_visited_nodes=["prior-id"]
        )
        assert state.has_visited("prior-id") is True
        assert state.has_visited("other") is False

    def test_all_visited_nodes(self) -> None:
        state = QueryAgentState(
            query="test",
            nav_budget=10,
            visited_nodes=["a", "b"],
            prior_visited_nodes=["b", "c"],
        )
        result = state.all_visited_nodes
        assert set(result) == {"a", "b", "c"}

    def test_no_explore_budget(self) -> None:
        """QueryAgentState should not have explore_budget at all."""
        state = QueryAgentState(query="test", nav_budget=10)
        assert not hasattr(state, "explore_budget")

    def test_hidden_nodes_default_empty(self) -> None:
        state = QueryAgentState(query="test", nav_budget=10)
        assert state.hidden_nodes == []


# ---------------------------------------------------------------------------
# Lightweight search tests
# ---------------------------------------------------------------------------


class TestLightweightSearchNodes:
    @pytest.mark.asyncio
    async def test_returns_graph_matches(self) -> None:
        ctx = _make_ctx()
        node = _make_mock_node(concept="Python programming")
        ctx.graph_engine.search_nodes = AsyncMock(return_value=[node])
        ctx.graph_engine.get_node_facts = AsyncMock(return_value=[_make_mock_fact()])
        ctx.graph_engine.get_dimensions = AsyncMock(return_value=[_make_mock_dimension()])

        result = await lightweight_search_nodes(["python"], ctx)

        assert "python" in result
        matches = result["python"]["graph_matches"]
        assert len(matches) == 1
        assert matches[0]["concept"] == "Python programming"
        assert matches[0]["fact_count"] == 1

    @pytest.mark.asyncio
    async def test_no_external_results(self) -> None:
        """Query search should not include external search results."""
        ctx = _make_ctx()
        result = await lightweight_search_nodes(["test"], ctx)

        assert "test" in result
        assert "external" not in result["test"]
        assert "graph_matches" in result["test"]

    @pytest.mark.asyncio
    async def test_deduplicates_text_and_embedding_matches(self) -> None:
        ctx = _make_ctx()
        node = _make_mock_node(concept="test node")
        ctx.graph_engine.search_nodes = AsyncMock(return_value=[node])
        ctx.graph_engine.find_similar_nodes = AsyncMock(return_value=[node])
        ctx.graph_engine.get_node_facts = AsyncMock(return_value=[])
        ctx.graph_engine.get_dimensions = AsyncMock(return_value=[])
        ctx.embedding_service.embed_batch = AsyncMock(return_value=[[0.1] * 10])

        result = await lightweight_search_nodes(["test"], ctx)
        # Should not duplicate the same node
        assert len(result["test"]["graph_matches"]) == 1


# ---------------------------------------------------------------------------
# Lightweight read_node tests
# ---------------------------------------------------------------------------


class TestLightweightReadNode:
    @pytest.mark.asyncio
    async def test_reads_node_successfully(self) -> None:
        ctx = _make_ctx()
        state = _make_state()
        node_id = uuid.uuid4()
        node = _make_mock_node(node_id=node_id, concept="Test Node")
        ctx.graph_engine.get_node = AsyncMock(return_value=node)
        ctx.graph_engine.get_dimensions = AsyncMock(return_value=[_make_mock_dimension()])
        ctx.graph_engine.get_edges = AsyncMock(return_value=[])
        ctx.graph_engine.get_node_facts = AsyncMock(return_value=[_make_mock_fact()])

        result = await lightweight_read_node(str(node_id), ctx, state)

        assert result["concept"] == "Test Node"
        assert result["budget_cost"] == 1
        assert result["fact_count"] == 1
        assert len(result["dimensions"]) == 1
        assert str(node_id) in state.visited_nodes

    @pytest.mark.asyncio
    async def test_no_enrichment_called(self) -> None:
        """Read node should NOT call enrich_node_from_pool."""
        ctx = _make_ctx()
        state = _make_state()
        node_id = uuid.uuid4()
        node = _make_mock_node(node_id=node_id)
        ctx.graph_engine.get_node = AsyncMock(return_value=node)

        await lightweight_read_node(str(node_id), ctx, state)

        # Verify enrichment-related methods were never called
        ctx.graph_engine.enrich_node_from_pool.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_access_count_increment(self) -> None:
        """Read node should NOT call increment_access_count."""
        ctx = _make_ctx()
        state = _make_state()
        node_id = uuid.uuid4()
        node = _make_mock_node(node_id=node_id)
        ctx.graph_engine.get_node = AsyncMock(return_value=node)

        await lightweight_read_node(str(node_id), ctx, state)

        ctx.graph_engine.increment_access_count.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_increment_access_count_on_get_facts(self) -> None:
        """get_node_facts should NOT increment access count."""
        ctx = _make_ctx()
        state = _make_state()
        node_id = uuid.uuid4()
        node = _make_mock_node(node_id=node_id)
        ctx.graph_engine.get_node = AsyncMock(return_value=node)

        await lightweight_get_node_facts(str(node_id), ctx, state)

        ctx.graph_engine.increment_access_count.assert_not_called()

    @pytest.mark.asyncio
    async def test_free_for_visited_node(self) -> None:
        ctx = _make_ctx()
        state = _make_state()
        node_id = uuid.uuid4()
        state.visited_nodes.append(str(node_id))
        node = _make_mock_node(node_id=node_id)
        ctx.graph_engine.get_node = AsyncMock(return_value=node)

        result = await lightweight_read_node(str(node_id), ctx, state)

        assert result["budget_cost"] == 0
        assert state.nav_used == 0

    @pytest.mark.asyncio
    async def test_budget_exhausted(self) -> None:
        ctx = _make_ctx()
        state = _make_state(nav_budget=0)
        node_id = uuid.uuid4()
        node = _make_mock_node(node_id=node_id)
        ctx.graph_engine.get_node = AsyncMock(return_value=node)

        result = await lightweight_read_node(str(node_id), ctx, state)

        assert "error" in result
        assert "Nav budget exhausted" in result["error"]

    @pytest.mark.asyncio
    async def test_emits_events(self) -> None:
        ctx = _make_ctx()
        state = _make_state()
        node_id = uuid.uuid4()
        node = _make_mock_node(node_id=node_id)
        ctx.graph_engine.get_node = AsyncMock(return_value=node)

        await lightweight_read_node(str(node_id), ctx, state)

        # Should emit node_visited and budget_update
        calls = ctx._emit_event.call_args_list
        event_types = [c[0][0] for c in calls]
        assert "activity_log" in event_types
        assert "node_visited" in event_types
        assert "budget_update" in event_types


# ---------------------------------------------------------------------------
# Lightweight get_node_facts tests
# ---------------------------------------------------------------------------


class TestLightweightGetNodeFacts:
    @pytest.mark.asyncio
    async def test_returns_facts(self) -> None:
        ctx = _make_ctx()
        state = _make_state()
        node_id = uuid.uuid4()
        node = _make_mock_node(node_id=node_id)
        ctx.graph_engine.get_node = AsyncMock(return_value=node)
        facts = [_make_mock_fact(content="Fact 1"), _make_mock_fact(content="Fact 2")]
        ctx.graph_engine.get_node_facts = AsyncMock(return_value=facts)

        result = await lightweight_get_node_facts(str(node_id), ctx, state)

        assert result["fact_count"] == 2
        assert len(result["facts"]) == 2
        assert result["budget_cost"] == 1

    @pytest.mark.asyncio
    async def test_no_access_count_increment(self) -> None:
        ctx = _make_ctx()
        state = _make_state()
        node_id = uuid.uuid4()
        node = _make_mock_node(node_id=node_id)
        ctx.graph_engine.get_node = AsyncMock(return_value=node)

        await lightweight_get_node_facts(str(node_id), ctx, state)

        ctx.graph_engine.increment_access_count.assert_not_called()

    @pytest.mark.asyncio
    async def test_free_for_visited(self) -> None:
        ctx = _make_ctx()
        state = _make_state()
        node_id = uuid.uuid4()
        state.visited_nodes.append(str(node_id))
        ctx.graph_engine.get_node_facts = AsyncMock(return_value=[_make_mock_fact()])

        result = await lightweight_get_node_facts(str(node_id), ctx, state)

        assert result["budget_cost"] == 0

    @pytest.mark.asyncio
    async def test_invalid_uuid(self) -> None:
        ctx = _make_ctx()
        state = _make_state()

        result = await lightweight_get_node_facts("not-a-uuid", ctx, state)

        assert "error" in result


# ---------------------------------------------------------------------------
# Tool factory tests
# ---------------------------------------------------------------------------


class TestCreateQueryTools:
    def test_returns_7_tools(self) -> None:
        ctx = _make_ctx()
        state = _make_state()
        tools = create_query_tools(ctx, lambda: state)

        assert len(tools) == 7

    def test_tool_names(self) -> None:
        ctx = _make_ctx()
        state = _make_state()
        tools = create_query_tools(ctx, lambda: state)
        names = {t.name for t in tools}

        assert names == {
            "search_graph",
            "read_node",
            "read_nodes",
            "get_node_facts",
            "get_node_facts_batch",
            "get_budget",
            "hide_nodes",
        }


# ---------------------------------------------------------------------------
# Hide nodes tool tests
# ---------------------------------------------------------------------------


class TestHideNodesTool:
    @pytest.mark.asyncio
    async def test_hide_emits_events(self) -> None:
        ctx = _make_ctx()
        state = _make_state()
        tools = create_query_tools(ctx, lambda: state)
        hide_tool = next(t for t in tools if t.name == "hide_nodes")

        result_raw = await hide_tool.ainvoke({"node_ids": ["abc", "def"]})
        result = json.loads(result_raw)

        assert result["hidden"] == 2
        assert result["total_hidden"] == 2
        assert len(state.hidden_nodes) == 2

        # Verify events emitted
        calls = ctx._emit_event.call_args_list
        hidden_calls = [c for c in calls if c[0][0] == "node_hidden"]
        assert len(hidden_calls) == 2

    @pytest.mark.asyncio
    async def test_hide_deduplicates(self) -> None:
        ctx = _make_ctx()
        state = _make_state()
        state.hidden_nodes = ["abc"]
        tools = create_query_tools(ctx, lambda: state)
        hide_tool = next(t for t in tools if t.name == "hide_nodes")

        result_raw = await hide_tool.ainvoke({"node_ids": ["abc", "def"]})
        result = json.loads(result_raw)

        assert result["hidden"] == 1  # Only "def" is new
        assert result["total_hidden"] == 2


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestSchemas:
    def test_create_conversation_request_mode_default(self) -> None:
        from kt_api.schemas import CreateConversationRequest

        req = CreateConversationRequest(message="test query")
        assert req.mode == "research"

    def test_create_conversation_request_mode_query(self) -> None:
        from kt_api.schemas import CreateConversationRequest

        req = CreateConversationRequest(message="test query", mode="query")
        assert req.mode == "query"

    def test_conversation_response_mode(self) -> None:
        from kt_api.schemas import ConversationResponse

        resp = ConversationResponse(
            id="test-id",
            title="test",
            mode="query",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
        )
        assert resp.mode == "query"

    def test_conversation_list_item_mode(self) -> None:
        from kt_api.schemas import ConversationListItem

        item = ConversationListItem(
            id="test-id",
            title="test",
            mode="query",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
        )
        assert item.mode == "query"


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestConfig:
    def test_query_agent_model_default(self) -> None:
        from kt_config.settings import Settings

        settings = Settings(
            database_url="postgresql+asyncpg://test:test@localhost:5432/test",
            openrouter_api_key="test-key",
        )
        assert settings.query_agent_model == ""
