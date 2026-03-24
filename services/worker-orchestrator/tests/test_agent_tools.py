"""Unit tests for agent tools with mocked dependencies."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kt_agents_core.state import AgentContext, PipelineState
from kt_worker_orchestrator.agents.tools.synthesize_answer import _extract_text_content, synthesize_answer_impl

pytestmark = pytest.mark.asyncio


def _make_mock_node(node_id: uuid.UUID | None = None, concept: str = "test_concept") -> MagicMock:
    """Create a mock Node object."""
    node = MagicMock()
    node.id = node_id or uuid.uuid4()
    node.concept = concept
    node.access_count = 0
    node.update_count = 0
    return node


def _make_mock_fact(
    fact_id: uuid.UUID | None = None, content: str = "test fact", fact_type: str = "claim"
) -> MagicMock:
    """Create a mock Fact object."""
    fact = MagicMock()
    fact.id = fact_id or uuid.uuid4()
    fact.content = content
    fact.fact_type = fact_type
    return fact


def _make_mock_edge(
    edge_id: uuid.UUID | None = None,
    source_id: uuid.UUID | None = None,
    target_id: uuid.UUID | None = None,
) -> MagicMock:
    """Create a mock Edge object."""
    edge = MagicMock()
    edge.id = edge_id or uuid.uuid4()
    edge.source_node_id = source_id or uuid.uuid4()
    edge.target_node_id = target_id or uuid.uuid4()
    edge.relationship_type = "related"
    return edge


def _make_mock_dimension(suggested_concepts: list[str] | None = None) -> MagicMock:
    """Create a mock Dimension object."""
    dim = MagicMock()
    dim.suggested_concepts = suggested_concepts
    return dim


def _make_ctx() -> AgentContext:
    """Create an AgentContext with all-mock dependencies."""
    graph_engine = AsyncMock()
    provider_registry = AsyncMock()
    model_gateway = AsyncMock()
    embedding_service = AsyncMock()
    session = AsyncMock()

    # Set up node repo mock
    graph_engine._node_repo = AsyncMock()

    # Write-db session mock (used by gathering pipeline)
    graph_engine._write_session = AsyncMock()

    # Make begin_nested() return a proper async context manager (sync call, async CM)
    nested_cm = AsyncMock()
    nested_cm.__aenter__ = AsyncMock(return_value=None)
    nested_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin_nested = MagicMock(return_value=nested_cm)

    return AgentContext(
        graph_engine=graph_engine,
        provider_registry=provider_registry,
        model_gateway=model_gateway,
        embedding_service=embedding_service,
        session=session,
    )


def _make_orchestrator_state(**kwargs: object) -> PipelineState:
    """Create an PipelineState with defaults."""
    defaults: dict[str, object] = {"query": "test query", "nav_budget": 10, "explore_budget": 5}
    defaults.update(kwargs)
    return PipelineState(**defaults)  # type: ignore[arg-type]


# ── _extract_text_content tests ────────────────────────────────────


def test_extract_text_content_string() -> None:
    assert _extract_text_content("hello world") == "hello world"


def test_extract_text_content_empty_string() -> None:
    assert _extract_text_content("") == ""


def test_extract_text_content_list_of_text_blocks() -> None:
    blocks = [
        {"type": "text", "text": "First paragraph."},
        {"type": "text", "text": "Second paragraph."},
    ]
    assert _extract_text_content(blocks) == "First paragraph.\nSecond paragraph."


def test_extract_text_content_thinking_and_text_blocks() -> None:
    """Models with extended thinking return thinking + text blocks."""
    blocks = [
        {"type": "thinking", "thinking": "Let me reason about this..."},
        {"type": "text", "text": "Here is the answer."},
    ]
    # Only text blocks should be extracted, not thinking blocks
    assert _extract_text_content(blocks) == "Here is the answer."


def test_extract_text_content_mixed_str_and_dict() -> None:
    blocks = ["plain string", {"type": "text", "text": "dict block"}]
    assert _extract_text_content(blocks) == "plain string\ndict block"


def test_extract_text_content_empty_list() -> None:
    assert _extract_text_content([]) == ""


# ── synthesize_answer tests ───────────────────────────────────────


def _make_synth_chat_model(responses: list[object]) -> MagicMock:
    """Create a mock ChatModel for the synthesis sub-agent."""
    mock = MagicMock()
    mock.bind_tools = MagicMock(return_value=mock)
    mock.ainvoke = AsyncMock(side_effect=responses)
    return mock


async def test_synthesize_answer_with_facts() -> None:
    """synthesize_answer_impl runs sub-agent that queries facts and finishes."""
    from langchain_core.messages import AIMessage

    ctx = _make_ctx()
    state = _make_orchestrator_state()
    node_id = uuid.uuid4()
    state.visited_nodes.append(str(node_id))

    # Mock get_node to return concept name
    mock_node = _make_mock_node(node_id, "water_properties")
    mock_node.node_type = "concept"
    mock_node.parent_id = None
    ctx.graph_engine.get_node = AsyncMock(return_value=mock_node)

    # Mock get_node_facts for node-list building
    mock_facts = [_make_mock_fact(content="Water boils at 100C")]
    ctx.graph_engine.get_node_facts = AsyncMock(return_value=mock_facts)

    # Mock get_node_facts_with_stance for the sub-agent's tool
    ctx.graph_engine.get_node_facts_with_stance = AsyncMock(return_value=[(mock_facts[0], None)])
    # Mock get_node_facts_with_sources for formatting
    ctx.graph_engine.get_node_facts_with_sources = AsyncMock(return_value=mock_facts)

    # Mock get_edges for graph consistency check
    ctx.graph_engine.get_edges = AsyncMock(return_value=[])

    # Sub-agent: first call get_node_facts, then call finish
    get_facts_msg = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "get_node_facts",
                "args": {"node_id": str(node_id)},
                "id": "synth_call_1",
            }
        ],
    )
    finish_msg = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "finish",
                "args": {"answer": "Water boils at 100 degrees Celsius."},
                "id": "synth_call_2",
            }
        ],
    )
    synth_chat = _make_synth_chat_model([get_facts_msg, finish_msg])
    ctx.model_gateway.get_chat_model = MagicMock(return_value=synth_chat)
    ctx.model_gateway.synthesis_model = "test-synthesis-model"

    result = await synthesize_answer_impl(ctx, state)
    assert result["fact_count"] == 1
    assert "Water boils" in str(result["answer"])
    assert state.phase == "synthesizing"


async def test_synthesize_answer_no_facts() -> None:
    """synthesize_answer_impl sub-agent handles no facts and still finishes."""
    from langchain_core.messages import AIMessage

    ctx = _make_ctx()
    state = _make_orchestrator_state()
    node_id = uuid.uuid4()
    state.visited_nodes.append(str(node_id))

    mock_node = _make_mock_node(node_id, "empty_concept")
    mock_node.node_type = "concept"
    mock_node.parent_id = None
    ctx.graph_engine.get_node = AsyncMock(return_value=mock_node)
    ctx.graph_engine.get_node_facts = AsyncMock(return_value=[])
    ctx.graph_engine.get_node_facts_with_stance = AsyncMock(return_value=[])
    ctx.graph_engine.get_node_facts_with_sources = AsyncMock(return_value=[])

    # Mock get_edges for graph consistency check
    ctx.graph_engine.get_edges = AsyncMock(return_value=[])

    get_facts_msg = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "get_node_facts",
                "args": {"node_id": str(node_id)},
                "id": "synth_call_1",
            }
        ],
    )
    finish_msg = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "finish",
                "args": {"answer": "No facts were available to answer the question."},
                "id": "synth_call_2",
            }
        ],
    )
    synth_chat = _make_synth_chat_model([get_facts_msg, finish_msg])
    ctx.model_gateway.get_chat_model = MagicMock(return_value=synth_chat)
    ctx.model_gateway.synthesis_model = "test-synthesis-model"

    result = await synthesize_answer_impl(ctx, state)
    assert result["fact_count"] == 0
    assert state.phase == "synthesizing"
    assert state.answer != ""


async def test_synthesize_answer_llm_error() -> None:
    """synthesize_answer_impl handles LLM errors gracefully."""
    ctx = _make_ctx()
    state = _make_orchestrator_state()
    node_id = uuid.uuid4()
    state.visited_nodes.append(str(node_id))

    mock_node = _make_mock_node(node_id, "error_concept")
    mock_node.node_type = "concept"
    mock_node.parent_id = None
    ctx.graph_engine.get_node = AsyncMock(return_value=mock_node)
    ctx.graph_engine.get_node_facts = AsyncMock(return_value=[])

    # Mock get_edges for graph consistency check
    ctx.graph_engine.get_edges = AsyncMock(return_value=[])

    # Sub-agent LLM raises an exception
    synth_chat = _make_synth_chat_model([])
    synth_chat.ainvoke = AsyncMock(side_effect=Exception("LLM error"))
    ctx.model_gateway.get_chat_model = MagicMock(return_value=synth_chat)
    ctx.model_gateway.synthesis_model = "test-synthesis-model"

    result = await synthesize_answer_impl(ctx, state)
    assert state.phase == "synthesizing"
    # Should have some answer (fallback or error message)
    assert state.answer != ""


async def test_synthesize_answer_no_visited_nodes() -> None:
    """synthesize_answer_impl exits early when no nodes were visited."""
    ctx = _make_ctx()
    state = _make_orchestrator_state()
    # No visited nodes

    result = await synthesize_answer_impl(ctx, state)
    assert result["fact_count"] == 0
    assert "No nodes" in str(result["answer"])
    assert state.phase == "synthesizing"


# ── read_node tests ──────────────────────────────────────────────


async def test_read_node_returns_dimensions_and_edges() -> None:
    """read_node_impl returns dimensions (with suggested_concepts) and edges."""
    from kt_worker_nodes.agents.tools.read_node import read_node_impl

    ctx = _make_ctx()
    state = _make_orchestrator_state(nav_budget=10)
    node_id = uuid.uuid4()
    target_id = uuid.uuid4()

    mock_node = _make_mock_node(node_id, "photosynthesis")
    mock_node.node_type = "concept"

    mock_target = _make_mock_node(target_id, "chloroplast")

    # Dimension with suggested_concepts
    dim = MagicMock()
    dim.model_id = "claude-3"
    dim.content = "Photosynthesis is the process by which plants convert sunlight."
    dim.confidence = 0.85
    dim.suggested_concepts = ["chloroplast", "sunlight", "carbon dioxide"]

    # Edge
    edge = _make_mock_edge(source_id=node_id, target_id=target_id)
    edge.weight = 0.7

    ctx.graph_engine.get_node = AsyncMock(side_effect=lambda nid: mock_node if nid == node_id else mock_target)
    ctx.graph_engine.get_dimensions = AsyncMock(return_value=[dim])
    ctx.graph_engine.get_edges = AsyncMock(return_value=[edge])
    ctx.graph_engine.get_node_facts = AsyncMock(return_value=[_make_mock_fact(), _make_mock_fact()])
    ctx.graph_engine.compute_richness = MagicMock(return_value=0.7)
    ctx.graph_engine.is_node_stale = MagicMock(return_value=False)

    result = await read_node_impl(str(node_id), ctx, state)

    assert result["node_id"] == str(node_id)
    assert result["concept"] == "photosynthesis"
    assert result["budget_cost"] == 1
    assert result["fact_count"] == 2
    assert result["richness"] == 0.7
    assert result["is_stale"] is False
    assert len(result["dimensions"]) == 1
    assert result["dimensions"][0]["suggested_concepts"] == ["chloroplast", "sunlight", "carbon dioxide"]
    assert len(result["edges"]) == 1
    assert result["edges"][0]["target_concept"] == "chloroplast"
    assert str(node_id) in state.visited_nodes


async def test_read_node_free_when_already_visited() -> None:
    """read_node_impl is free when node is already in visited_nodes."""
    from kt_worker_nodes.agents.tools.read_node import read_node_impl

    ctx = _make_ctx()
    node_id = uuid.uuid4()
    state = _make_orchestrator_state(nav_budget=10, nav_used=1, visited_nodes=[str(node_id)])

    mock_node = _make_mock_node(node_id, "water")
    mock_node.node_type = "concept"

    ctx.graph_engine.get_node = AsyncMock(return_value=mock_node)
    ctx.graph_engine.get_dimensions = AsyncMock(return_value=[])
    ctx.graph_engine.get_edges = AsyncMock(return_value=[])
    ctx.graph_engine.get_node_facts = AsyncMock(return_value=[])
    ctx.graph_engine.compute_richness = MagicMock(return_value=0.0)
    ctx.graph_engine.is_node_stale = MagicMock(return_value=False)

    result = await read_node_impl(str(node_id), ctx, state)

    assert result["budget_cost"] == 0
    assert result["nav_remaining"] == 9  # 10 - 1 already visited


async def test_read_node_error_when_nav_exhausted() -> None:
    """read_node_impl returns error when nav budget is exhausted for unvisited node."""
    from kt_worker_nodes.agents.tools.read_node import read_node_impl

    ctx = _make_ctx()
    node_id = uuid.uuid4()
    state = _make_orchestrator_state(nav_budget=2, nav_used=2, visited_nodes=["node1", "node2"])

    mock_node = _make_mock_node(node_id, "new_concept")
    ctx.graph_engine.get_node = AsyncMock(return_value=mock_node)

    result = await read_node_impl(str(node_id), ctx, state)

    assert "error" in result
    assert "Nav budget exhausted" in result["error"]
    assert str(node_id) not in state.visited_nodes


async def test_read_node_error_invalid_uuid() -> None:
    """read_node_impl returns error for invalid UUID string."""
    from kt_worker_nodes.agents.tools.read_node import read_node_impl

    ctx = _make_ctx()
    state = _make_orchestrator_state()

    result = await read_node_impl("not-a-uuid", ctx, state)

    assert "error" in result
    assert "Invalid node_id" in result["error"]


async def test_read_node_error_missing_node() -> None:
    """read_node_impl returns error when node doesn't exist."""
    from kt_worker_nodes.agents.tools.read_node import read_node_impl

    ctx = _make_ctx()
    state = _make_orchestrator_state()
    node_id = uuid.uuid4()

    ctx.graph_engine.get_node = AsyncMock(return_value=None)

    result = await read_node_impl(str(node_id), ctx, state)

    assert "error" in result
    assert "Node not found" in result["error"]


async def test_read_node_perspective_no_stance_summary() -> None:
    """read_node_impl does NOT include stance_summary for perspective nodes."""
    from kt_worker_nodes.agents.tools.read_node import read_node_impl

    ctx = _make_ctx()
    state = _make_orchestrator_state(nav_budget=10)
    node_id = uuid.uuid4()

    mock_node = _make_mock_node(node_id, "the moon is artificial")
    mock_node.node_type = "perspective"

    ctx.graph_engine.get_node = AsyncMock(return_value=mock_node)
    ctx.graph_engine.get_dimensions = AsyncMock(return_value=[])
    ctx.graph_engine.get_edges = AsyncMock(return_value=[])
    ctx.graph_engine.get_node_facts = AsyncMock(return_value=[])
    ctx.graph_engine.compute_richness = MagicMock(return_value=0.0)
    ctx.graph_engine.is_node_stale = MagicMock(return_value=False)

    result = await read_node_impl(str(node_id), ctx, state)

    assert "stance_summary" not in result
    assert "node_type" in result
    assert result["node_type"] == "perspective"


async def test_read_node_adds_to_exploration_path() -> None:
    """read_node_impl adds the concept name to exploration_path."""
    from kt_worker_nodes.agents.tools.read_node import read_node_impl

    ctx = _make_ctx()
    state = _make_orchestrator_state(nav_budget=10)
    node_id = uuid.uuid4()

    mock_node = _make_mock_node(node_id, "chloroplast")
    mock_node.node_type = "concept"

    ctx.graph_engine.get_node = AsyncMock(return_value=mock_node)
    ctx.graph_engine.get_dimensions = AsyncMock(return_value=[])
    ctx.graph_engine.get_edges = AsyncMock(return_value=[])
    ctx.graph_engine.get_node_facts = AsyncMock(return_value=[])
    ctx.graph_engine.compute_richness = MagicMock(return_value=0.0)
    ctx.graph_engine.is_node_stale = MagicMock(return_value=False)

    result = await read_node_impl(str(node_id), ctx, state)

    assert "chloroplast" in state.exploration_path


# ── batch read_nodes tests ──────────────────────────────────────


async def test_read_nodes_returns_results_for_each() -> None:
    """read_nodes_impl returns one result per node ID."""
    from kt_worker_nodes.agents.tools.read_node import read_nodes_impl

    ctx = _make_ctx()
    state = _make_orchestrator_state(nav_budget=10)
    ids = [uuid.uuid4() for _ in range(3)]

    for nid in ids:
        mock_node = _make_mock_node(nid, f"concept_{nid}")
        mock_node.node_type = "concept"

        async def make_get_node(expected_id: uuid.UUID = nid, mn: MagicMock = mock_node):  # type: ignore[assignment]
            return mn

    # Set up graph_engine mocks
    nodes_by_id = {}
    for nid in ids:
        node = _make_mock_node(nid, f"concept_{nid}")
        node.node_type = "concept"
        nodes_by_id[nid] = node

    async def get_node_side_effect(node_id: uuid.UUID) -> MagicMock | None:
        return nodes_by_id.get(node_id)

    ctx.graph_engine.get_node = AsyncMock(side_effect=get_node_side_effect)
    ctx.graph_engine.get_dimensions = AsyncMock(return_value=[])
    ctx.graph_engine.get_edges = AsyncMock(return_value=[])
    ctx.graph_engine.get_node_facts = AsyncMock(return_value=[])
    ctx.graph_engine.compute_richness = MagicMock(return_value=0.0)
    ctx.graph_engine.is_node_stale = MagicMock(return_value=False)

    result = await read_nodes_impl([str(nid) for nid in ids], ctx, state)

    assert result["count"] == 3
    assert len(result["results"]) == 3
    assert result["errors"] == 0
    assert result["capped"] is False
    # All three should have been visited
    for nid in ids:
        assert str(nid) in state.visited_nodes


async def test_read_nodes_budget_tracking() -> None:
    """read_nodes_impl tracks budget correctly for mix of visited/unvisited."""
    from kt_worker_nodes.agents.tools.read_node import read_nodes_impl

    ctx = _make_ctx()
    id_visited = uuid.uuid4()
    id_new = uuid.uuid4()
    state = _make_orchestrator_state(nav_budget=10, visited_nodes=[str(id_visited)])

    nodes_by_id = {}
    for nid in [id_visited, id_new]:
        node = _make_mock_node(nid, f"concept_{nid}")
        node.node_type = "concept"
        nodes_by_id[nid] = node

    ctx.graph_engine.get_node = AsyncMock(side_effect=lambda nid: nodes_by_id.get(nid))
    ctx.graph_engine.get_dimensions = AsyncMock(return_value=[])
    ctx.graph_engine.get_edges = AsyncMock(return_value=[])
    ctx.graph_engine.get_node_facts = AsyncMock(return_value=[])
    ctx.graph_engine.compute_richness = MagicMock(return_value=0.0)
    ctx.graph_engine.is_node_stale = MagicMock(return_value=False)

    result = await read_nodes_impl([str(id_visited), str(id_new)], ctx, state)

    assert result["count"] == 2
    assert result["total_budget_cost"] == 1  # Only the new one costs
    # First result (visited) should be free
    assert result["results"][0]["budget_cost"] == 0
    # Second result (unvisited) should cost 1
    assert result["results"][1]["budget_cost"] == 1


async def test_read_nodes_partial_failure() -> None:
    """read_nodes_impl handles invalid IDs gracefully alongside valid ones."""
    from kt_worker_nodes.agents.tools.read_node import read_nodes_impl

    ctx = _make_ctx()
    state = _make_orchestrator_state(nav_budget=10)
    valid_id = uuid.uuid4()

    node = _make_mock_node(valid_id, "valid_concept")
    node.node_type = "concept"
    ctx.graph_engine.get_node = AsyncMock(return_value=node)
    ctx.graph_engine.get_dimensions = AsyncMock(return_value=[])
    ctx.graph_engine.get_edges = AsyncMock(return_value=[])
    ctx.graph_engine.get_node_facts = AsyncMock(return_value=[])
    ctx.graph_engine.compute_richness = MagicMock(return_value=0.0)
    ctx.graph_engine.is_node_stale = MagicMock(return_value=False)

    result = await read_nodes_impl(["not-a-uuid", str(valid_id)], ctx, state)

    assert result["count"] == 2
    assert result["errors"] == 1  # Invalid UUID
    assert "error" in result["results"][0]
    assert result["results"][1]["concept"] == "valid_concept"


async def test_read_nodes_caps_at_max_batch_size() -> None:
    """read_nodes_impl caps at MAX_BATCH_SIZE items."""
    from kt_worker_nodes.agents.tools.read_node import MAX_BATCH_SIZE, read_nodes_impl

    ctx = _make_ctx()
    state = _make_orchestrator_state(nav_budget=20)

    # Create more IDs than MAX_BATCH_SIZE
    ids = [uuid.uuid4() for _ in range(MAX_BATCH_SIZE + 5)]
    nodes_by_id = {}
    for nid in ids:
        node = _make_mock_node(nid, f"concept_{nid}")
        node.node_type = "concept"
        nodes_by_id[nid] = node

    ctx.graph_engine.get_node = AsyncMock(side_effect=lambda nid: nodes_by_id.get(nid))
    ctx.graph_engine.get_dimensions = AsyncMock(return_value=[])
    ctx.graph_engine.get_edges = AsyncMock(return_value=[])
    ctx.graph_engine.get_node_facts = AsyncMock(return_value=[])
    ctx.graph_engine.compute_richness = MagicMock(return_value=0.0)
    ctx.graph_engine.is_node_stale = MagicMock(return_value=False)

    result = await read_nodes_impl([str(nid) for nid in ids], ctx, state)

    assert result["count"] == MAX_BATCH_SIZE
    assert result["capped"] is True


# ── batch build_concepts tests ──────────────────────────────────


async def test_build_nodes_returns_results_for_each() -> None:
    """build_nodes_impl returns one result per node entry."""
    from kt_worker_nodes.agents.tools.build_node import build_nodes_impl

    ctx = _make_ctx()
    state = _make_orchestrator_state(explore_budget=5)

    # Mock: all concepts are new and pool has facts
    ctx.graph_engine.search_nodes = AsyncMock(return_value=[])
    ctx.graph_engine.search_nodes_by_trigram = AsyncMock(return_value=[])
    ctx.embedding_service.embed_text = AsyncMock(return_value=[0.1] * 10)
    ctx.embedding_service.embed_batch = AsyncMock(return_value=[[0.1] * 10, [0.1] * 10, [0.1] * 10])
    ctx.graph_engine.find_similar_nodes = AsyncMock(return_value=[])
    pool_fact = _make_mock_fact(content="a relevant fact")
    ctx.graph_engine.search_fact_pool = AsyncMock(return_value=[pool_fact])
    ctx.graph_engine.search_fact_pool_text = AsyncMock(return_value=[])

    new_node = _make_mock_node(concept="test")
    new_node.node_type = "concept"
    ctx.graph_engine.create_node = AsyncMock(return_value=new_node)
    ctx.graph_engine.link_fact_to_node = AsyncMock()
    ctx.graph_engine.get_dimensions = AsyncMock(return_value=[])
    ctx.graph_engine.get_node_facts_with_sources = AsyncMock(return_value=[])
    ctx.model_gateway.dimension_model = "test-model"

    entries = [{"name": n, "node_type": "concept"} for n in ["alpha", "beta", "gamma"]]
    with patch(
        "kt_worker_nodes.pipelines.dimensions.pipeline.generate_dimensions", new_callable=AsyncMock, return_value=[]
    ):
        result = await build_nodes_impl(entries, ctx, state)

    assert result["count"] == 3
    assert len(result["results"]) == 3
    assert result["capped"] is False


async def test_build_nodes_caps_at_max_batch_size() -> None:
    """build_nodes_impl caps at MAX_BATCH_SIZE items."""
    from kt_worker_nodes.agents.tools.build_node import MAX_BATCH_SIZE, build_nodes_impl

    ctx = _make_ctx()
    state = _make_orchestrator_state(explore_budget=20)

    # Mock for quick skips (no pool facts, no budget)
    state.explore_used = 20  # exhaust budget
    ctx.graph_engine.search_nodes = AsyncMock(return_value=[])
    ctx.graph_engine.search_nodes_by_trigram = AsyncMock(return_value=[])
    ctx.embedding_service.embed_text = AsyncMock(return_value=[0.1] * 10)
    ctx.embedding_service.embed_batch = AsyncMock(side_effect=lambda names: [[0.1] * 10] * len(names))
    ctx.graph_engine.find_similar_nodes = AsyncMock(return_value=[])
    ctx.graph_engine.search_fact_pool = AsyncMock(return_value=[])
    ctx.graph_engine.search_fact_pool_text = AsyncMock(return_value=[])

    entries = [{"name": f"concept_{i}", "node_type": "concept"} for i in range(MAX_BATCH_SIZE + 5)]
    result = await build_nodes_impl(entries, ctx, state)

    assert result["count"] == MAX_BATCH_SIZE
    assert result["capped"] is True


async def test_build_nodes_actions_summary() -> None:
    """build_nodes_impl returns correct actions_summary."""
    from kt_worker_nodes.agents.tools.build_node import build_nodes_impl

    ctx = _make_ctx()
    state = _make_orchestrator_state(explore_budget=0, explore_used=0)

    # All concepts will be skipped (no explore budget, no pool facts)
    ctx.graph_engine.search_nodes = AsyncMock(return_value=[])
    ctx.graph_engine.search_nodes_by_trigram = AsyncMock(return_value=[])
    ctx.embedding_service.embed_text = AsyncMock(return_value=[0.1] * 10)
    ctx.embedding_service.embed_batch = AsyncMock(return_value=[[0.1] * 10, [0.1] * 10])
    ctx.graph_engine.find_similar_nodes = AsyncMock(return_value=[])
    ctx.graph_engine.search_fact_pool = AsyncMock(return_value=[])
    ctx.graph_engine.search_fact_pool_text = AsyncMock(return_value=[])

    entries = [{"name": n, "node_type": "concept"} for n in ["x", "y"]]
    result = await build_nodes_impl(entries, ctx, state)

    assert result["count"] == 2
    assert "skipped" in result["actions_summary"]
    assert result["actions_summary"]["skipped"] == 2


# ── parallel provider registry tests ──────────────────────────────


async def test_search_all_single_string_returns_list() -> None:
    """search_all with a single string returns list (backwards compatible)."""
    from kt_config.types import RawSearchResult
    from kt_providers.registry import ProviderRegistry

    registry = ProviderRegistry()
    provider = AsyncMock()
    provider.provider_id = "test"
    provider.is_available = AsyncMock(return_value=True)
    provider.search = AsyncMock(
        return_value=[
            RawSearchResult(title="Result 1", uri="http://a.com", raw_content="content", provider_id="test"),
        ]
    )
    registry.register(provider)

    result = await registry.search_all("test query")
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0].title == "Result 1"


async def test_search_all_list_returns_dict() -> None:
    """search_all with a list of queries returns dict keyed by query."""
    from kt_config.types import RawSearchResult
    from kt_providers.registry import ProviderRegistry

    registry = ProviderRegistry()
    provider = AsyncMock()
    provider.provider_id = "test"
    provider.is_available = AsyncMock(return_value=True)

    async def mock_search(query: str, max_results: int = 10) -> list[RawSearchResult]:
        return [
            RawSearchResult(title=f"Result for {query}", uri=f"http://{query}.com", raw_content="", provider_id="test")
        ]

    provider.search = AsyncMock(side_effect=mock_search)
    registry.register(provider)

    result = await registry.search_all(["query1", "query2", "query3"])
    assert isinstance(result, dict)
    assert len(result) == 3
    assert "query1" in result
    assert "query2" in result
    assert "query3" in result
    assert result["query1"][0].title == "Result for query1"


async def test_search_all_list_error_handling() -> None:
    """search_all with list handles errors for individual queries gracefully."""
    from kt_providers.registry import ProviderRegistry

    registry = ProviderRegistry()
    provider = AsyncMock()
    provider.provider_id = "test"
    provider.is_available = AsyncMock(return_value=True)

    call_count = {"n": 0}

    async def mock_search(query: str, max_results: int = 10) -> list:  # type: ignore[type-arg]
        call_count["n"] += 1
        if "fail" in query:
            raise ValueError("Search failed")
        from kt_config.types import RawSearchResult

        return [RawSearchResult(title=f"OK {query}", uri=f"http://{query}.com", raw_content="", provider_id="test")]

    provider.search = AsyncMock(side_effect=mock_search)
    registry.register(provider)

    result = await registry.search_all(["good_query", "fail_query", "another_good"])
    assert isinstance(result, dict)
    assert len(result) == 3
    assert len(result["good_query"]) == 1
    assert len(result["fail_query"]) == 0  # Error → empty list
    assert len(result["another_good"]) == 1


# ── scout batch operations tests ──────────────────────────────────


async def test_scout_uses_batch_search() -> None:
    """scout_impl calls search_all with full query list (batch mode)."""
    from kt_worker_orchestrator.agents.tools.scout import scout_impl

    ctx = _make_ctx()
    queries = ["query1", "query2"]

    # Mock: search_all receives the list and returns dict
    ctx.provider_registry.search_all = AsyncMock(
        return_value={
            "query1": [],
            "query2": [],
        }
    )
    ctx.embedding_service.embed_batch = AsyncMock(return_value=[[0.1] * 10, [0.2] * 10])
    ctx.graph_engine.search_nodes = AsyncMock(return_value=[])
    ctx.graph_engine.find_similar_nodes = AsyncMock(return_value=[])

    result = await scout_impl(queries, ctx)

    # search_all should have been called with the full list
    ctx.provider_registry.search_all.assert_called_once_with(queries, max_results=5)
    # embed_batch should have been called with the full list
    ctx.embedding_service.embed_batch.assert_called_once_with(queries)

    assert "query1" in result
    assert "query2" in result


async def test_gather_facts_uses_batch_search() -> None:
    """gather_facts_impl calls search_all with all affordable queries."""
    from kt_worker_nodes.pipelines.gathering import GatherFactsPipeline

    ctx = _make_ctx()
    state = _make_orchestrator_state(explore_budget=5)

    # Mock: search_all receives the list and returns dict
    from kt_config.types import RawSearchResult

    mock_results = {
        "q1": [RawSearchResult(title="r1", uri="http://r1.com", raw_content="content1", provider_id="test")],
        "q2": [RawSearchResult(title="r2", uri="http://r2.com", raw_content="content2", provider_id="test")],
    }
    ctx.provider_registry.search_all = AsyncMock(return_value=mock_results)

    mock_page_log = MagicMock()
    mock_page_log.check_urls_freshness = AsyncMock(return_value={})
    mock_page_log.record_fetch = AsyncMock()

    mock_settings = MagicMock()
    mock_settings.full_text_fetch_per_budget_point = 10
    mock_settings.fetch_guarantee_max_rounds = 1
    mock_settings.page_stale_days = 30

    with patch(
        "kt_worker_nodes.pipelines.gathering.pipeline.store_and_fetch", new_callable=AsyncMock, return_value=[]
    ) as mock_store:
        with patch(
            "kt_worker_nodes.pipelines.gathering.pipeline.WritePageFetchLogRepository", return_value=mock_page_log
        ):
            with patch("kt_worker_nodes.pipelines.gathering.pipeline.get_settings", return_value=mock_settings):
                with patch("kt_worker_nodes.pipelines.gathering.pipeline.DecompositionPipeline") as MockPipeline:
                    MockPipeline.return_value.decompose = AsyncMock(return_value=[])
                    result = await GatherFactsPipeline(ctx).gather(["q1", "q2"], state)

    # search_all should have been called with the full list
    ctx.provider_registry.search_all.assert_called_once_with(["q1", "q2"], max_results=10)
    assert result["queries_executed"] == 2
    # Verify source titles and perspective reminder are included
    assert result["source_titles_by_query"] == {"q1": ["r1"], "q2": ["r2"]}


# ── enrich_node_from_pool / relation re-discovery tests ───────────


async def test_enrich_node_from_pool_returns_enrich_result() -> None:
    """enrich_node_from_pool returns an EnrichResult — links facts only, no dim regen."""
    from kt_worker_nodes.pipelines.enrichment import PoolEnricher
    from kt_worker_nodes.pipelines.models import EnrichResult

    ctx = _make_ctx()
    node = _make_mock_node(concept="enrich_test")
    node.embedding = [0.1] * 10

    existing_fact = _make_mock_fact(content="existing fact")
    new_fact = _make_mock_fact(content="new pool fact")

    ctx.graph_engine.get_node_facts = AsyncMock(return_value=[existing_fact])
    ctx.graph_engine.search_fact_pool_excluding_rejected = AsyncMock(return_value=[new_fact])
    ctx.graph_engine.search_fact_pool_text_excluding_rejected = AsyncMock(return_value=[])
    ctx.graph_engine.link_fact_to_node = AsyncMock()
    ctx.embedding_service = AsyncMock()

    result = await PoolEnricher(ctx).enrich(node)

    assert isinstance(result, EnrichResult)
    assert result.new_facts_linked == 1
    # PoolEnricher no longer regenerates dimensions — that's the DAG's job
    assert result.dimensions_regenerated is False


async def test_enrich_node_from_pool_below_threshold() -> None:
    """enrich_node_from_pool does NOT regenerate dimensions below threshold."""
    from kt_worker_nodes.pipelines.enrichment import PoolEnricher
    from kt_worker_nodes.pipelines.models import EnrichResult

    ctx = _make_ctx()
    node = _make_mock_node(concept="enrich_below_test")
    node.embedding = [0.1] * 10

    # 10 existing facts, 1 new = ratio 0.1 < 0.25
    existing_facts = [_make_mock_fact(content=f"fact {i}") for i in range(10)]
    new_fact = _make_mock_fact(content="new pool fact")

    ctx.graph_engine.get_node_facts = AsyncMock(return_value=existing_facts)
    ctx.graph_engine.search_fact_pool_excluding_rejected = AsyncMock(return_value=[new_fact])
    ctx.graph_engine.search_fact_pool_text_excluding_rejected = AsyncMock(return_value=[])
    ctx.graph_engine.link_fact_to_node = AsyncMock()
    ctx.embedding_service = AsyncMock()

    result = await PoolEnricher(ctx).enrich(node)

    assert isinstance(result, EnrichResult)
    assert result.new_facts_linked == 1
    assert result.dimensions_regenerated is False


async def test_build_node_enrich_does_not_regenerate_dimensions() -> None:
    """build_node_unified enrichment links facts but does NOT regenerate dimensions."""
    from kt_worker_nodes.pipelines.building.unified import UnifiedNodeBuilder

    ctx = _make_ctx()
    state = _make_orchestrator_state(explore_budget=5)

    node = _make_mock_node(concept="enrich_regen_test")
    node.node_type = "concept"
    ctx.graph_engine.search_nodes = AsyncMock(return_value=[node])
    ctx.graph_engine.is_node_stale = MagicMock(return_value=False)

    existing_fact = _make_mock_fact(content="existing")
    new_fact = _make_mock_fact(content="new pool")
    ctx.graph_engine.get_node_facts = AsyncMock(
        side_effect=[
            [existing_fact],  # First call in enrich
            [existing_fact, new_fact],  # Second call after enrichment
        ]
    )
    ctx.graph_engine.search_fact_pool_excluding_rejected = AsyncMock(return_value=[new_fact])
    ctx.graph_engine.search_fact_pool_text_excluding_rejected = AsyncMock(return_value=[])
    ctx.graph_engine.link_fact_to_node = AsyncMock()
    ctx.graph_engine.get_dimensions = AsyncMock(return_value=[])
    ctx.embedding_service = AsyncMock()

    with patch(
        "kt_worker_nodes.pipelines.building.helpers.generate_and_store_dimensions",
        new_callable=AsyncMock,
        return_value=(0, set()),
    ) as mock_gen_dims:
        result = await UnifiedNodeBuilder(ctx).build("enrich_regen_test", "concept", ctx, state)

    assert result["action"] == "enriched"
    # Enrichment no longer triggers dimension generation — the Hatchet DAG handles it
    mock_gen_dims.assert_not_called()


async def test_build_node_enrich_below_threshold_no_regen() -> None:
    """build_node_unified does NOT regenerate dimensions when below threshold."""
    from kt_worker_nodes.pipelines.building.unified import UnifiedNodeBuilder

    ctx = _make_ctx()
    state = _make_orchestrator_state(explore_budget=5)

    node = _make_mock_node(concept="no_regen_test")
    node.node_type = "concept"
    ctx.graph_engine.search_nodes = AsyncMock(return_value=[node])
    ctx.graph_engine.is_node_stale = MagicMock(return_value=False)

    # 10 existing, 1 new → ratio=0.1 < 0.25 → no regen
    existing_facts = [_make_mock_fact(content=f"fact {i}") for i in range(10)]
    new_fact = _make_mock_fact(content="new pool")
    ctx.graph_engine.get_node_facts = AsyncMock(
        side_effect=[
            existing_facts,  # First call in enrich_node_from_pool
            existing_facts + [new_fact],  # Second call after enrichment
        ]
    )
    ctx.graph_engine.search_fact_pool_excluding_rejected = AsyncMock(return_value=[new_fact])
    ctx.graph_engine.search_fact_pool_text_excluding_rejected = AsyncMock(return_value=[])
    ctx.graph_engine.link_fact_to_node = AsyncMock()
    ctx.graph_engine.get_dimensions = AsyncMock(return_value=[])
    ctx.embedding_service = AsyncMock()

    result = await UnifiedNodeBuilder(ctx).build("no_regen_test", "concept", ctx, state)

    assert result["action"] == "enriched"


# ── resolve_edges tests ──────────────────────────────────────────


async def test_resolve_edges_no_candidates() -> None:
    """resolve_from_candidates returns empty when no candidates in write-db."""
    from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

    ctx = _make_ctx()
    node = _make_mock_node(concept="isolated_node")
    node.node_type = "concept"

    write_session = MagicMock()
    ctx.graph_engine._write_session = write_session

    with patch("kt_worker_nodes.pipelines.edges.resolver.WriteSeedRepository") as MockRepo:
        mock_repo = MockRepo.return_value
        mock_repo.get_candidates_for_seed = AsyncMock(return_value=[])

        result = await EdgeResolver(ctx).resolve_from_candidates(node)

    assert result["edges_created"] == 0
    assert result["edge_ids"] == []


async def test_resolve_edges_no_write_session() -> None:
    """resolve_from_candidates returns empty when write session is unavailable."""
    from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

    ctx = _make_ctx()
    node = _make_mock_node(concept="no_session_node")
    node.node_type = "concept"
    ctx.graph_engine._write_session = None

    result = await EdgeResolver(ctx).resolve_from_candidates(node)

    assert result["edges_created"] == 0
    assert result["edge_ids"] == []


async def test_resolve_edges_creates_edge_from_candidates() -> None:
    """resolve_from_candidates creates an edge when candidate seed is promoted."""
    from kt_db.keys import key_to_uuid, make_seed_key
    from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

    ctx = _make_ctx()
    node = _make_mock_node(concept="quantum computing")
    node.node_type = "concept"
    node.id = key_to_uuid(make_seed_key("concept", "quantum computing"))

    write_session = MagicMock()
    ctx.graph_engine._write_session = write_session

    source_key = make_seed_key("concept", "quantum computing")
    target_key = make_seed_key("concept", "quantum mechanics")
    fact_id = uuid.uuid4()

    candidate_row = MagicMock()
    candidate_row.seed_key_a = source_key
    candidate_row.seed_key_b = target_key
    candidate_row.fact_id = str(fact_id)
    candidate_row.status = "pending"

    target_seed = MagicMock()
    target_seed.key = target_key
    target_seed.name = "quantum mechanics"
    target_seed.node_type = "concept"
    target_seed.promoted_node_key = target_key

    mock_fact = _make_mock_fact(fact_id=fact_id, content="QC uses QM principles")
    ctx.graph_engine.get_facts_by_ids = AsyncMock(return_value=[mock_fact])

    mock_edge = _make_mock_edge()
    ctx.graph_engine.create_edge = AsyncMock(return_value=mock_edge)

    ctx.model_gateway.generate_json = AsyncMock(
        return_value=[
            {
                "justification": "Quantum computing relies on quantum mechanics via {fact:1}",
            }
        ]
    )

    with patch("kt_worker_nodes.pipelines.edges.resolver.WriteSeedRepository") as MockRepo:
        mock_repo = MockRepo.return_value
        mock_repo.get_candidates_for_seed = AsyncMock(return_value=[candidate_row])
        mock_repo.get_seed_by_key = AsyncMock(return_value=target_seed)
        mock_repo.accept_candidate_facts = AsyncMock()

        result = await EdgeResolver(ctx).resolve_from_candidates(node)

    assert result["edges_created"] == 1
    assert len(result["edge_ids"]) == 1
    ctx.graph_engine.create_edge.assert_called_once()


async def test_resolve_edges_skips_unpromoted_seed() -> None:
    """resolve_from_candidates skips candidates where target seed is not promoted."""
    from kt_db.keys import key_to_uuid, make_seed_key
    from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

    ctx = _make_ctx()
    node = _make_mock_node(concept="physics")
    node.node_type = "concept"
    node.id = key_to_uuid(make_seed_key("concept", "physics"))

    write_session = MagicMock()
    ctx.graph_engine._write_session = write_session

    source_key = make_seed_key("concept", "physics")
    target_key = make_seed_key("concept", "thermodynamics")
    fact_id = uuid.uuid4()

    candidate_row = MagicMock()
    candidate_row.seed_key_a = source_key
    candidate_row.seed_key_b = target_key
    candidate_row.fact_id = str(fact_id)
    candidate_row.status = "pending"

    # Target seed not promoted
    unpromoted_seed = MagicMock()
    unpromoted_seed.key = target_key
    unpromoted_seed.name = "thermodynamics"
    unpromoted_seed.node_type = "concept"
    unpromoted_seed.promoted_node_key = None

    with patch("kt_worker_nodes.pipelines.edges.resolver.WriteSeedRepository") as MockRepo:
        mock_repo = MockRepo.return_value
        mock_repo.get_candidates_for_seed = AsyncMock(return_value=[candidate_row])
        mock_repo.get_seed_by_key = AsyncMock(return_value=unpromoted_seed)

        result = await EdgeResolver(ctx).resolve_from_candidates(node)

    assert result["edges_created"] == 0


async def test_resolve_edges_cross_type_detected() -> None:
    """resolve_from_candidates uses cross_type for different node types."""
    from kt_db.keys import key_to_uuid, make_seed_key
    from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

    ctx = _make_ctx()
    node = _make_mock_node(concept="Albert Einstein")
    node.node_type = "entity"
    node.id = key_to_uuid(make_seed_key("entity", "Albert Einstein"))

    write_session = MagicMock()
    ctx.graph_engine._write_session = write_session

    source_key = make_seed_key("entity", "Albert Einstein")
    target_key = make_seed_key("concept", "relativity")
    fact_id = uuid.uuid4()

    candidate_row = MagicMock()
    candidate_row.seed_key_a = source_key
    candidate_row.seed_key_b = target_key
    candidate_row.fact_id = str(fact_id)
    candidate_row.status = "pending"

    target_seed = MagicMock()
    target_seed.key = target_key
    target_seed.name = "relativity"
    target_seed.node_type = "concept"
    target_seed.promoted_node_key = target_key

    mock_fact = _make_mock_fact(fact_id=fact_id, content="Einstein developed relativity")
    ctx.graph_engine.get_facts_by_ids = AsyncMock(return_value=[mock_fact])

    mock_edge = _make_mock_edge()
    ctx.graph_engine.create_edge = AsyncMock(return_value=mock_edge)

    ctx.model_gateway.generate_json = AsyncMock(
        return_value=[
            {
                "justification": "Einstein developed the theory of relativity",
            }
        ]
    )

    with patch("kt_worker_nodes.pipelines.edges.resolver.WriteSeedRepository") as MockRepo:
        mock_repo = MockRepo.return_value
        mock_repo.get_candidates_for_seed = AsyncMock(return_value=[candidate_row])
        mock_repo.get_seed_by_key = AsyncMock(return_value=target_seed)
        mock_repo.accept_candidate_facts = AsyncMock()

        result = await EdgeResolver(ctx).resolve_from_candidates(node)

    assert result["edges_created"] == 1
    # Verify cross_type was used
    call_args = ctx.graph_engine.create_edge.call_args
    assert call_args[0][2] == "cross_type"


async def test_resolve_edges_handles_llm_error() -> None:
    """resolve_from_candidates handles LLM errors gracefully."""
    from kt_db.keys import key_to_uuid, make_seed_key
    from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

    ctx = _make_ctx()
    node = _make_mock_node(concept="error_node")
    node.node_type = "concept"
    node.id = key_to_uuid(make_seed_key("concept", "error_node"))

    write_session = MagicMock()
    ctx.graph_engine._write_session = write_session

    source_key = make_seed_key("concept", "error_node")
    target_key = make_seed_key("concept", "other")
    fact_id = uuid.uuid4()

    candidate_row = MagicMock()
    candidate_row.seed_key_a = source_key
    candidate_row.seed_key_b = target_key
    candidate_row.fact_id = str(fact_id)
    candidate_row.status = "pending"

    target_seed = MagicMock()
    target_seed.key = target_key
    target_seed.name = "other"
    target_seed.node_type = "concept"
    target_seed.promoted_node_key = target_key

    mock_fact = _make_mock_fact(fact_id=fact_id, content="Some fact")
    ctx.graph_engine.get_facts_by_ids = AsyncMock(return_value=[mock_fact])
    ctx.graph_engine.create_edge = AsyncMock(return_value=_make_mock_edge())

    # LLM call raises an error — edge should still be created with empty justification
    ctx.model_gateway.generate_json = AsyncMock(side_effect=Exception("API error"))

    with patch("kt_worker_nodes.pipelines.edges.resolver.WriteSeedRepository") as MockRepo:
        mock_repo = MockRepo.return_value
        mock_repo.get_candidates_for_seed = AsyncMock(return_value=[candidate_row])
        mock_repo.get_seed_by_key = AsyncMock(return_value=target_seed)
        mock_repo.accept_candidate_facts = AsyncMock()

        result = await EdgeResolver(ctx).resolve_from_candidates(node)

    # Edge still created (LLM error only affects justification)
    assert result["edges_created"] == 1


# ── _parse_llm_decisions tests ───────────────────────────────────


def test_parse_llm_decisions_array_in_dict() -> None:
    """_parse_llm_decisions extracts justifications from various dict structures."""
    from kt_worker_nodes.pipelines.edges.classifier import EdgeClassifier
    from kt_worker_nodes.pipelines.models import EdgeCandidate

    candidates = [
        EdgeCandidate(
            source_node_id=uuid.uuid4(),
            source_concept="A",
            target_node_id=uuid.uuid4(),
            target_concept="B",
            evidence_fact_ids=[uuid.uuid4()],
        )
    ]

    # Test with list of justification objects
    llm_result = [{"justification": "A and B are related via fact 1"}]
    decisions = EdgeClassifier.parse_llm_decisions(llm_result, candidates)
    assert decisions[0] is not None
    assert decisions[0]["justification"] == "A and B are related via fact 1"

    # Test with single-pair dict containing justification
    llm_result2 = {"justification": "direct connection"}
    decisions2 = EdgeClassifier.parse_llm_decisions(llm_result2, candidates)
    assert decisions2[0] is not None
    assert decisions2[0]["justification"] == "direct connection"


def test_parse_llm_decisions_empty_result() -> None:
    """_parse_llm_decisions returns None entries for empty LLM response."""
    from kt_worker_nodes.pipelines.edges.classifier import EdgeClassifier
    from kt_worker_nodes.pipelines.models import EdgeCandidate

    candidates = [
        EdgeCandidate(
            source_node_id=uuid.uuid4(),
            source_concept="X",
            target_node_id=uuid.uuid4(),
            target_concept="Y",
            evidence_fact_ids=[uuid.uuid4()],
        )
    ]

    decisions = EdgeClassifier.parse_llm_decisions({}, candidates)
    assert decisions[0] is None


# ── _build_classification_prompt fact capping tests ───────────────


def test_build_classification_prompt_caps_facts() -> None:
    """Per-type cap is enforced in classification prompt."""
    from kt_worker_nodes.pipelines.edges.classifier import EdgeClassifier
    from kt_worker_nodes.pipelines.models import EdgeCandidate

    # 25 facts of type "claim" — should be capped to per_type_cap=5
    facts = [_make_mock_fact(fact_id=uuid.uuid4(), content=f"claim {i}", fact_type="claim") for i in range(25)]
    candidate = EdgeCandidate(
        source_node_id=uuid.uuid4(),
        source_concept="A",
        target_node_id=uuid.uuid4(),
        target_concept="B",
        evidence_fact_ids=[f.id for f in facts],
        evidence_facts=facts,
    )

    prompt, fact_maps = EdgeClassifier.build_classification_prompt(
        [candidate], facts_per_type_cap=5, facts_per_candidate_cap=50
    )

    # Count fact lines (lines starting with numbered list items)
    fact_lines = [line for line in prompt.split("\n") if line.strip().startswith(tuple(f"{i}." for i in range(1, 30)))]
    assert len(fact_lines) == 5
    # fact_maps should have one entry (one candidate) with 5 mappings
    assert len(fact_maps) == 1
    assert len(fact_maps[0]) == 5


def test_build_classification_prompt_total_cap() -> None:
    """Total cap is enforced even when multiple types are below per-type cap."""
    from kt_worker_nodes.pipelines.edges.classifier import EdgeClassifier
    from kt_worker_nodes.pipelines.models import EdgeCandidate

    # 10 claims + 10 definitions = 20 total, cap total at 8
    claims = [_make_mock_fact(fact_id=uuid.uuid4(), content=f"claim {i}", fact_type="claim") for i in range(10)]
    defs = [_make_mock_fact(fact_id=uuid.uuid4(), content=f"def {i}", fact_type="definition") for i in range(10)]
    all_facts = claims + defs

    candidate = EdgeCandidate(
        source_node_id=uuid.uuid4(),
        source_concept="A",
        target_node_id=uuid.uuid4(),
        target_concept="B",
        evidence_fact_ids=[f.id for f in all_facts],
        evidence_facts=all_facts,
    )

    prompt, fact_maps = EdgeClassifier.build_classification_prompt(
        [candidate], facts_per_type_cap=20, facts_per_candidate_cap=8
    )

    fact_lines = [line for line in prompt.split("\n") if line.strip().startswith(tuple(f"{i}." for i in range(1, 30)))]
    assert len(fact_lines) == 8
    assert len(fact_maps[0]) == 8


def test_build_classification_prompt_labels_evidence_source() -> None:
    """Facts are tagged with their fact_type only (no ownership tags)."""
    from kt_worker_nodes.pipelines.edges.classifier import EdgeClassifier
    from kt_worker_nodes.pipelines.models import EdgeCandidate

    fact1 = _make_mock_fact(fact_id=uuid.uuid4(), content="shared content", fact_type="claim")
    fact2 = _make_mock_fact(fact_id=uuid.uuid4(), content="another content", fact_type="statistic")

    candidate = EdgeCandidate(
        source_node_id=uuid.uuid4(),
        source_concept="A",
        target_node_id=uuid.uuid4(),
        target_concept="B",
        evidence_fact_ids=[fact1.id, fact2.id],
        evidence_facts=[fact1, fact2],
    )

    prompt, fact_maps = EdgeClassifier.build_classification_prompt([candidate])

    # Should have fact_type tags, not ownership tags
    assert "[claim]" in prompt
    assert "[statistic]" in prompt
    assert "[shared]" not in prompt
    assert "[related]" not in prompt
    assert "[A]" not in prompt
    assert "[B]" not in prompt
    assert "[A+B]" not in prompt
    # Fact map should map index 1 → fact1.id, index 2 → fact2.id
    assert fact_maps[0][1] == fact1.id
    assert fact_maps[0][2] == fact2.id


# ── _resolve_fact_tokens tests ────────────────────────────────────


def test_resolve_fact_tokens_replaces_indices() -> None:
    """_resolve_fact_tokens replaces {fact:N} with {fact:<uuid>}."""
    from kt_worker_nodes.pipelines.models import resolve_fact_tokens as _resolve_fact_tokens

    fid1 = uuid.uuid4()
    fid3 = uuid.uuid4()
    idx_map = {1: fid1, 2: uuid.uuid4(), 3: fid3}

    raw = "Supported by {fact:1} and {fact:3}, weak link to {fact:2}"
    result = _resolve_fact_tokens(raw, idx_map)

    assert f"{{fact:{fid1}}}" in result
    assert f"{{fact:{fid3}}}" in result
    assert "{fact:1}" not in result
    assert "{fact:3}" not in result


def test_resolve_fact_tokens_leaves_unknown_indices() -> None:
    """_resolve_fact_tokens leaves {fact:N} unchanged when N is not in map."""
    from kt_worker_nodes.pipelines.models import resolve_fact_tokens as _resolve_fact_tokens

    fid1 = uuid.uuid4()
    idx_map = {1: fid1}

    raw = "Based on {fact:1} and {fact:99}"
    result = _resolve_fact_tokens(raw, idx_map)

    assert f"{{fact:{fid1}}}" in result
    # Unknown index should stay as-is
    assert "{fact:99}" in result


def test_resolve_fact_tokens_no_tokens() -> None:
    """_resolve_fact_tokens returns string unchanged when no tokens present."""
    from kt_worker_nodes.pipelines.models import resolve_fact_tokens as _resolve_fact_tokens

    raw = "A justification without any fact references"
    result = _resolve_fact_tokens(raw, {1: uuid.uuid4()})
    assert result == raw


# ── _cap_facts_by_type tests ─────────────────────────────────────


def test_cap_facts_by_type_preserves_diversity() -> None:
    """Round-robin under total cap preserves diversity across fact types."""
    from kt_worker_nodes.pipelines.edges.classifier import cap_facts_by_type as _cap_facts_by_type

    claims = [_make_mock_fact(fact_id=uuid.uuid4(), content=f"claim {i}", fact_type="claim") for i in range(10)]
    defs = [_make_mock_fact(fact_id=uuid.uuid4(), content=f"def {i}", fact_type="definition") for i in range(10)]
    stats = [_make_mock_fact(fact_id=uuid.uuid4(), content=f"stat {i}", fact_type="statistic") for i in range(10)]
    all_facts = claims + defs + stats  # 30 total

    result = _cap_facts_by_type(all_facts, per_type_cap=20, total_cap=9)

    assert len(result) == 9
    # Should have 3 of each type (round-robin: 3 types × 3 each = 9)
    type_counts: dict[str, int] = {}
    for f in result:
        type_counts[f.fact_type] = type_counts.get(f.fact_type, 0) + 1
    assert type_counts["claim"] == 3
    assert type_counts["definition"] == 3
    assert type_counts["statistic"] == 3


# ── _classify_in_batches tests ────────────────────────────────────


async def test_classify_in_batches() -> None:
    """25 candidates split into 3 batches (10+10+5), all decisions collected."""
    from typing import Any

    from kt_worker_nodes.pipelines.edges.classifier import EdgeClassifier
    from kt_worker_nodes.pipelines.models import EdgeCandidate

    ctx = _make_ctx()

    # Create 25 candidates with facts
    candidates: list[EdgeCandidate] = []
    for i in range(25):
        fact = _make_mock_fact(fact_id=uuid.uuid4(), content=f"fact {i}")
        candidates.append(
            EdgeCandidate(
                source_node_id=uuid.uuid4(),
                source_concept=f"source_{i}",
                target_node_id=uuid.uuid4(),
                target_concept=f"target_{i}",
                evidence_fact_ids=[fact.id],
                evidence_facts=[fact],
            )
        )

    # LLM returns valid justifications for each batch
    call_count = {"n": 0}

    async def mock_generate_json(**kwargs: Any) -> Any:
        call_count["n"] += 1
        prompt_text = kwargs["messages"][0]["content"]
        pair_count = prompt_text.count("--- Pair")
        return [{"justification": f"batch {call_count['n']} pair {j}"} for j in range(pair_count)]

    ctx.model_gateway.generate_json = AsyncMock(side_effect=mock_generate_json)

    classifier = EdgeClassifier(ctx)
    decisions = await classifier.classify(candidates, batch_size=10)

    # 3 LLM calls: 10 + 10 + 5
    assert call_count["n"] == 3
    assert len(decisions) == 25
    # All should have valid decisions
    assert all(d is not None for d in decisions)
    assert all("justification" in d for d in decisions if d is not None)


async def test_resolve_edges_multiple_candidates_batch() -> None:
    """Multiple candidate pairs are all resolved in one call."""
    from kt_db.keys import key_to_uuid, make_seed_key
    from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

    ctx = _make_ctx()
    node = _make_mock_node(concept="photosynthesis")
    node.node_type = "concept"
    node.id = key_to_uuid(make_seed_key("concept", "photosynthesis"))

    write_session = MagicMock()
    ctx.graph_engine._write_session = write_session

    source_key = make_seed_key("concept", "photosynthesis")
    target_key_a = make_seed_key("concept", "chloroplast")
    target_key_b = make_seed_key("concept", "sunlight")
    fid_a = uuid.uuid4()
    fid_b = uuid.uuid4()

    row_a = MagicMock()
    row_a.seed_key_a = source_key
    row_a.seed_key_b = target_key_a
    row_a.fact_id = str(fid_a)
    row_a.status = "pending"

    row_b = MagicMock()
    row_b.seed_key_a = source_key
    row_b.seed_key_b = target_key_b
    row_b.fact_id = str(fid_b)
    row_b.status = "pending"

    seed_a = MagicMock()
    seed_a.key = target_key_a
    seed_a.name = "chloroplast"
    seed_a.node_type = "concept"
    seed_a.promoted_node_key = target_key_a

    seed_b = MagicMock()
    seed_b.key = target_key_b
    seed_b.name = "sunlight"
    seed_b.node_type = "concept"
    seed_b.promoted_node_key = target_key_b

    fact_a = _make_mock_fact(fact_id=fid_a, content="chloroplast performs photosynthesis")
    fact_b = _make_mock_fact(fact_id=fid_b, content="sunlight powers photosynthesis")

    ctx.graph_engine.get_facts_by_ids = AsyncMock(side_effect=lambda ids: [fact_a] if fid_a in ids else [fact_b])
    ctx.graph_engine.create_edge = AsyncMock(return_value=_make_mock_edge())
    ctx.model_gateway.generate_json = AsyncMock(
        return_value=[{"justification": "connected via shared photosynthesis facts"}]
    )

    with patch("kt_worker_nodes.pipelines.edges.resolver.WriteSeedRepository") as MockRepo:
        mock_repo = MockRepo.return_value
        mock_repo.get_candidates_for_seed = AsyncMock(return_value=[row_a, row_b])
        mock_repo.get_seed_by_key = AsyncMock(side_effect=lambda k: seed_a if k == target_key_a else seed_b)
        mock_repo.accept_candidate_facts = AsyncMock()

        result = await EdgeResolver(ctx).resolve_from_candidates(node)

    assert result["edges_created"] == 2
    assert len(result["edge_ids"]) == 2
