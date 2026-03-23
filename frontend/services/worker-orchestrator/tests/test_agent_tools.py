"""Unit tests for agent tools with mocked dependencies."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kt_worker_orchestrator.agents.orchestrator_state import OrchestratorState
from kt_agents_core.state import AgentContext
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

def _make_orchestrator_state(**kwargs: object) -> OrchestratorState:
    """Create an OrchestratorState with defaults."""
    defaults: dict[str, object] = {"query": "test query", "nav_budget": 10, "explore_budget": 5}
    defaults.update(kwargs)
    return OrchestratorState(**defaults)  # type: ignore[arg-type]

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
    ctx.graph_engine.get_node_facts_with_stance = AsyncMock(
        return_value=[(mock_facts[0], None)]
    )
    # Mock get_node_facts_with_sources for formatting
    ctx.graph_engine.get_node_facts_with_sources = AsyncMock(return_value=mock_facts)

    # Mock get_edges for graph consistency check
    ctx.graph_engine.get_edges = AsyncMock(return_value=[])

    # Sub-agent: first call get_node_facts, then call finish
    get_facts_msg = AIMessage(
        content="",
        tool_calls=[{
            "name": "get_node_facts",
            "args": {"node_id": str(node_id)},
            "id": "synth_call_1",
        }],
    )
    finish_msg = AIMessage(
        content="",
        tool_calls=[{
            "name": "finish",
            "args": {"answer": "Water boils at 100 degrees Celsius."},
            "id": "synth_call_2",
        }],
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
        tool_calls=[{
            "name": "get_node_facts",
            "args": {"node_id": str(node_id)},
            "id": "synth_call_1",
        }],
    )
    finish_msg = AIMessage(
        content="",
        tool_calls=[{
            "name": "finish",
            "args": {"answer": "No facts were available to answer the question."},
            "id": "synth_call_2",
        }],
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
    with patch("kt_worker_nodes.pipelines.dimensions.pipeline.generate_dimensions", new_callable=AsyncMock, return_value=[]):
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
    from kt_providers.registry import ProviderRegistry
    from kt_config.types import RawSearchResult

    registry = ProviderRegistry()
    provider = AsyncMock()
    provider.provider_id = "test"
    provider.is_available = AsyncMock(return_value=True)
    provider.search = AsyncMock(return_value=[
        RawSearchResult(title="Result 1", uri="http://a.com", raw_content="content", provider_id="test"),
    ])
    registry.register(provider)

    result = await registry.search_all("test query")
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0].title == "Result 1"

async def test_search_all_list_returns_dict() -> None:
    """search_all with a list of queries returns dict keyed by query."""
    from kt_providers.registry import ProviderRegistry
    from kt_config.types import RawSearchResult

    registry = ProviderRegistry()
    provider = AsyncMock()
    provider.provider_id = "test"
    provider.is_available = AsyncMock(return_value=True)

    async def mock_search(query: str, max_results: int = 10) -> list[RawSearchResult]:
        return [RawSearchResult(title=f"Result for {query}", uri=f"http://{query}.com", raw_content="", provider_id="test")]

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
    ctx.provider_registry.search_all = AsyncMock(return_value={
        "query1": [],
        "query2": [],
    })
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

    with patch("kt_worker_nodes.pipelines.gathering.pipeline.store_and_fetch", new_callable=AsyncMock, return_value=[]) as mock_store:
        with patch("kt_worker_nodes.pipelines.gathering.pipeline.DecompositionPipeline") as MockPipeline:
            MockPipeline.return_value.decompose = AsyncMock(return_value=[])
            result = await GatherFactsPipeline(ctx).gather(["q1", "q2"], state)

    # search_all should have been called with the full list
    ctx.provider_registry.search_all.assert_called_once_with(["q1", "q2"], max_results=5)
    assert result["queries_executed"] == 2
    # Verify source titles and perspective reminder are included
    assert result["source_titles_by_query"] == {"q1": ["r1"], "q2": ["r2"]}
    assert "perspective_reminder" in result
    assert "opposing position" in result["perspective_reminder"]

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
    ctx.graph_engine.get_node_facts = AsyncMock(side_effect=[
        [existing_fact],  # First call in enrich
        [existing_fact, new_fact],  # Second call after enrichment
    ])
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
    ctx.graph_engine.get_node_facts = AsyncMock(side_effect=[
        existing_facts,  # First call in enrich_node_from_pool
        existing_facts + [new_fact],  # Second call after enrichment
    ])
    ctx.graph_engine.search_fact_pool_excluding_rejected = AsyncMock(return_value=[new_fact])
    ctx.graph_engine.search_fact_pool_text_excluding_rejected = AsyncMock(return_value=[])
    ctx.graph_engine.link_fact_to_node = AsyncMock()
    ctx.graph_engine.get_dimensions = AsyncMock(return_value=[])
    ctx.embedding_service = AsyncMock()

    result = await UnifiedNodeBuilder(ctx).build("no_regen_test", "concept", ctx, state)

    assert result["action"] == "enriched"

# ── resolve_edges tests ──────────────────────────────────────────

async def test_resolve_edges_no_evidence_facts() -> None:
    """resolve_edges returns empty when no nodes share facts."""
    from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

    ctx = _make_ctx()
    node = _make_mock_node(concept="isolated_node")

    ctx.graph_engine.find_nodes_sharing_facts = AsyncMock(return_value=[])

    result = await EdgeResolver(ctx).resolve(node)

    assert result["edges_created"] == 0
    assert result["edge_ids"] == []

async def test_resolve_edges_skips_recent_pairs() -> None:
    """resolve_edges filters out candidate pairs with recent edges."""
    from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

    ctx = _make_ctx()
    node = _make_mock_node(concept="source_node")
    node.node_type = "concept"
    other_id = uuid.uuid4()
    fact_id = uuid.uuid4()

    # Mock candidate node for same-type filtering
    other_node = _make_mock_node(other_id, "other_node")
    other_node.node_type = "concept"
    ctx.graph_engine.get_nodes_by_ids = AsyncMock(return_value=[other_node])

    # One candidate that shares a fact
    ctx.graph_engine.find_nodes_sharing_facts = AsyncMock(
        return_value=[(other_id, "other_node", [fact_id])]
    )
    # But the pair already has a recent edge
    ctx.graph_engine.get_recent_edge_pairs = AsyncMock(return_value={other_id})

    result = await EdgeResolver(ctx).resolve(node)

    assert result["edges_created"] == 0

async def test_resolve_edges_creates_edge_from_llm() -> None:
    """resolve_edges creates edges when LLM classifies relationships."""
    from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

    ctx = _make_ctx()
    node = _make_mock_node(concept="quantum_computing")
    node.node_type = "concept"
    other_id = uuid.uuid4()
    fact_id = uuid.uuid4()

    # Mock candidate node for same-type filtering
    other_node = _make_mock_node(other_id, "quantum_mechanics")
    other_node.node_type = "concept"
    ctx.graph_engine.get_nodes_by_ids = AsyncMock(return_value=[other_node])

    # One candidate sharing a fact
    ctx.graph_engine.find_nodes_sharing_facts = AsyncMock(
        return_value=[(other_id, "quantum_mechanics", [fact_id])]
    )
    ctx.graph_engine.get_recent_edge_pairs = AsyncMock(return_value=set())
    ctx.graph_engine.get_evaluated_fact_ids_batch = AsyncMock(return_value={})

    # Mock fact loading
    mock_fact = _make_mock_fact(fact_id=fact_id, content="Quantum computing relies on quantum mechanics")
    ctx.graph_engine._fact_repo = AsyncMock()
    ctx.graph_engine._fact_repo.get_by_id = AsyncMock(return_value=mock_fact)
    ctx.graph_engine.get_facts_by_ids = AsyncMock(return_value=[mock_fact])

    # Mock LLM response
    ctx.model_gateway.generate_json = AsyncMock(return_value={
        "results": [{
            "relationship_type": "related",
            "weight": 0.8,
            "justification": "Quantum computing is a subfield of quantum mechanics per fact 1",
            "direction": "a_to_b",
        }]
    })

    # Mock edge creation + evaluation tracking
    mock_edge = _make_mock_edge()
    ctx.graph_engine.create_edge = AsyncMock(return_value=mock_edge)
    ctx.graph_engine.link_fact_to_edge = AsyncMock()
    ctx.graph_engine.clear_evaluations_for_pair = AsyncMock(return_value=0)

    result = await EdgeResolver(ctx).resolve(node)

    assert result["edges_created"] == 1
    assert len(result["edge_ids"]) == 1
    ctx.graph_engine.create_edge.assert_called_once()

async def test_resolve_edges_skips_none_decisions() -> None:
    """resolve_edges skips pairs where LLM returns 'none'."""
    from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

    ctx = _make_ctx()
    node = _make_mock_node(concept="unrelated_a")
    node.node_type = "concept"
    other_id = uuid.uuid4()
    fact_id = uuid.uuid4()

    # Mock candidate node for same-type filtering
    other_node = _make_mock_node(other_id, "unrelated_b")
    other_node.node_type = "concept"
    ctx.graph_engine.get_nodes_by_ids = AsyncMock(return_value=[other_node])

    ctx.graph_engine.find_nodes_sharing_facts = AsyncMock(
        return_value=[(other_id, "unrelated_b", [fact_id])]
    )
    ctx.graph_engine.get_recent_edge_pairs = AsyncMock(return_value=set())
    ctx.graph_engine.get_evaluated_fact_ids_batch = AsyncMock(return_value={})
    ctx.graph_engine.record_negative_evaluations = AsyncMock(return_value=1)

    mock_fact = _make_mock_fact(fact_id=fact_id, content="A general fact")
    ctx.graph_engine._fact_repo = AsyncMock()
    ctx.graph_engine._fact_repo.get_by_id = AsyncMock(return_value=mock_fact)
    ctx.graph_engine.get_facts_by_ids = AsyncMock(return_value=[mock_fact])

    # LLM says "none"
    ctx.model_gateway.generate_json = AsyncMock(return_value={
        "results": [{
            "relationship_type": "none",
            "weight": 0.0,
            "justification": "No meaningful relationship",
        }]
    })

    result = await EdgeResolver(ctx).resolve(node)

    assert result["edges_created"] == 0
    ctx.graph_engine.create_edge = AsyncMock()  # Should not be called
    assert ctx.graph_engine.create_edge.call_count == 0
    # Negative evaluation should have been recorded
    ctx.graph_engine.record_negative_evaluations.assert_called_once()

async def test_resolve_edges_handles_llm_error() -> None:
    """resolve_edges handles LLM errors gracefully."""
    from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

    ctx = _make_ctx()
    node = _make_mock_node(concept="error_node")
    node.node_type = "concept"
    other_id = uuid.uuid4()
    fact_id = uuid.uuid4()

    # Mock candidate node for same-type filtering
    other_node = _make_mock_node(other_id, "other")
    other_node.node_type = "concept"
    ctx.graph_engine.get_nodes_by_ids = AsyncMock(return_value=[other_node])

    ctx.graph_engine.find_nodes_sharing_facts = AsyncMock(
        return_value=[(other_id, "other", [fact_id])]
    )
    ctx.graph_engine.get_recent_edge_pairs = AsyncMock(return_value=set())
    ctx.graph_engine.get_evaluated_fact_ids_batch = AsyncMock(return_value={})
    ctx.graph_engine.record_negative_evaluations = AsyncMock(return_value=1)

    mock_fact = _make_mock_fact(fact_id=fact_id, content="Some fact")
    ctx.graph_engine._fact_repo = AsyncMock()
    ctx.graph_engine._fact_repo.get_by_id = AsyncMock(return_value=mock_fact)
    ctx.graph_engine.get_facts_by_ids = AsyncMock(return_value=[mock_fact])

    # LLM call raises an error
    ctx.model_gateway.generate_json = AsyncMock(side_effect=Exception("API error"))

    result = await EdgeResolver(ctx).resolve(node)

    assert result["edges_created"] == 0
    assert result["edge_ids"] == []

# ── _parse_llm_decisions tests ───────────────────────────────────

def test_parse_llm_decisions_array_in_dict() -> None:
    """_parse_llm_decisions extracts decisions from various dict structures."""
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

    # Test with "results" key
    llm_result = {"results": [{"relationship_type": "related", "weight": 0.7, "justification": "test"}]}
    decisions = EdgeClassifier.parse_llm_decisions(llm_result, candidates)
    assert decisions[0] is not None
    assert decisions[0]["relationship_type"] == "related"

    # Test with single-pair dict
    llm_result2 = {"relationship_type": "related", "weight": 0.5, "justification": "causal"}
    decisions2 = EdgeClassifier.parse_llm_decisions(llm_result2, candidates)
    assert decisions2[0] is not None
    assert decisions2[0]["relationship_type"] == "related"

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

# ── resolve_edges: embedding candidates tests ─────────────────────

async def test_resolve_edges_shared_fact_candidates() -> None:
    """Shared-fact candidates create edges when LLM classifies them."""
    from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

    ctx = _make_ctx()
    node = _make_mock_node(concept="machine_learning")
    node.node_type = "concept"
    other_id = uuid.uuid4()
    fact_id = uuid.uuid4()

    # Mock candidate node for same-type filtering
    other_node = _make_mock_node(other_id, "neural_networks")
    other_node.node_type = "concept"
    ctx.graph_engine.get_nodes_by_ids = AsyncMock(return_value=[other_node])

    # Shared-fact candidate
    ctx.graph_engine.find_nodes_sharing_facts = AsyncMock(
        return_value=[(other_id, "neural_networks", [fact_id])]
    )
    ctx.graph_engine.get_recent_edge_pairs = AsyncMock(return_value=set())
    ctx.graph_engine.get_evaluated_fact_ids_batch = AsyncMock(return_value={})

    # Mock fact loading
    mock_fact = _make_mock_fact(fact_id=fact_id, content="Neural nets are ML models")
    ctx.graph_engine._fact_repo = AsyncMock()
    ctx.graph_engine._fact_repo.get_by_id = AsyncMock(return_value=mock_fact)
    ctx.graph_engine.get_facts_by_ids = AsyncMock(return_value=[mock_fact])

    # Mock LLM response
    ctx.model_gateway.generate_json = AsyncMock(return_value={
        "results": [{
            "relationship_type": "related",
            "weight": 0.7,
            "justification": "Neural networks are a subset of ML per fact 1",
            "direction": "a_to_b",
        }]
    })

    # Mock edge creation + evaluation tracking
    mock_edge = _make_mock_edge()
    ctx.graph_engine.create_edge = AsyncMock(return_value=mock_edge)
    ctx.graph_engine.link_fact_to_edge = AsyncMock()
    ctx.graph_engine.clear_evaluations_for_pair = AsyncMock(return_value=0)

    result = await EdgeResolver(ctx).resolve(node)

    assert result["edges_created"] == 1
    ctx.graph_engine.find_nodes_sharing_facts.assert_called_once()

async def test_resolve_edges_multiple_evidence_facts() -> None:
    """Candidate with multiple shared facts reports correct evidence count."""
    from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

    ctx = _make_ctx()
    node = _make_mock_node(concept="photosynthesis")
    node.node_type = "concept"
    other_id = uuid.uuid4()
    fid1 = uuid.uuid4()
    fid2 = uuid.uuid4()

    # Mock candidate node for same-type filtering
    other_node = _make_mock_node(other_id, "chloroplast")
    other_node.node_type = "concept"
    ctx.graph_engine.get_nodes_by_ids = AsyncMock(return_value=[other_node])

    # Candidate shares two facts
    ctx.graph_engine.find_nodes_sharing_facts = AsyncMock(
        return_value=[(other_id, "chloroplast", [fid1, fid2])]
    )
    ctx.graph_engine.get_recent_edge_pairs = AsyncMock(return_value=set())
    ctx.graph_engine.get_evaluated_fact_ids_batch = AsyncMock(return_value={})

    # Mock fact loading
    fact1 = _make_mock_fact(fact_id=fid1, content="shared fact 1")
    fact2 = _make_mock_fact(fact_id=fid2, content="shared fact 2")
    ctx.graph_engine._fact_repo = AsyncMock()
    ctx.graph_engine._fact_repo.get_by_id = AsyncMock(
        side_effect=lambda fid: fact1 if fid == fid1 else fact2
    )
    ctx.graph_engine.get_facts_by_ids = AsyncMock(return_value=[fact1, fact2])

    # LLM returns a valid decision
    ctx.model_gateway.generate_json = AsyncMock(return_value={
        "results": [{"relationship_type": "related", "weight": 0.8, "justification": "test"}]
    })

    resolution = await EdgeResolver(ctx).discover_and_classify(node)

    assert resolution is not None
    assert len(resolution.candidates) == 1
    candidate = resolution.candidates[0]
    assert fid1 in candidate.evidence_fact_ids
    assert fid2 in candidate.evidence_fact_ids
    assert candidate.evidence_count == 2

async def test_resolve_edges_staleness_filter() -> None:
    """Candidates with recent edges are skipped."""
    from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

    ctx = _make_ctx()
    node = _make_mock_node(concept="stale_test")
    node.node_type = "concept"
    stale_id = uuid.uuid4()
    fact_id = uuid.uuid4()

    # Mock candidate node for same-type filtering
    stale_node = _make_mock_node(stale_id, "stale_node")
    stale_node.node_type = "concept"
    ctx.graph_engine.get_nodes_by_ids = AsyncMock(return_value=[stale_node])

    ctx.graph_engine.find_nodes_sharing_facts = AsyncMock(
        return_value=[(stale_id, "stale_node", [fact_id])]
    )
    # The candidate has a recent edge — should be filtered out
    ctx.graph_engine.get_recent_edge_pairs = AsyncMock(return_value={stale_id})

    resolution = await EdgeResolver(ctx).discover_and_classify(node)

    # All candidates filtered → None
    assert resolution is None

async def test_resolve_edges_no_embedding_on_node() -> None:
    """Shared-fact discovery works even when node.embedding=None."""
    from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

    ctx = _make_ctx()
    node = _make_mock_node(concept="no_embed_node")
    node.node_type = "concept"
    node.embedding = None
    other_id = uuid.uuid4()
    fact_id = uuid.uuid4()

    # Mock candidate node for same-type filtering
    other_node = _make_mock_node(other_id, "other")
    other_node.node_type = "concept"
    ctx.graph_engine.get_nodes_by_ids = AsyncMock(return_value=[other_node])

    ctx.graph_engine.find_nodes_sharing_facts = AsyncMock(
        return_value=[(other_id, "other", [fact_id])]
    )
    ctx.graph_engine.get_recent_edge_pairs = AsyncMock(return_value=set())
    ctx.graph_engine.get_evaluated_fact_ids_batch = AsyncMock(return_value={})

    mock_fact = _make_mock_fact(fact_id=fact_id, content="a fact")
    ctx.graph_engine._fact_repo = AsyncMock()
    ctx.graph_engine._fact_repo.get_by_id = AsyncMock(return_value=mock_fact)
    ctx.graph_engine.get_facts_by_ids = AsyncMock(return_value=[mock_fact])

    ctx.model_gateway.generate_json = AsyncMock(return_value={
        "results": [{"relationship_type": "related", "weight": 0.6, "justification": "test"}]
    })

    resolution = await EdgeResolver(ctx).discover_and_classify(node)

    assert resolution is not None
    assert len(resolution.candidates) == 1

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

    prompt, fact_maps = EdgeClassifier.build_classification_prompt([candidate], facts_per_type_cap=5, facts_per_candidate_cap=50)

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

    prompt, fact_maps = EdgeClassifier.build_classification_prompt([candidate], facts_per_type_cap=20, facts_per_candidate_cap=8)

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

async def test_resolve_edges_records_negative_evaluations() -> None:
    """apply_edge_decisions records negative evaluations for 'none' decisions."""
    from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver
    from kt_worker_nodes.pipelines.models import EdgeCandidate, EdgeResolution

    ctx = _make_ctx()
    fact_id = uuid.uuid4()
    source_id = uuid.uuid4()
    target_id = uuid.uuid4()

    candidate = EdgeCandidate(
        source_node_id=source_id,
        source_concept="concept_a",
        target_node_id=target_id,
        target_concept="concept_b",
        evidence_fact_ids=[fact_id],
        evidence_facts=[_make_mock_fact(fact_id=fact_id, content="a fact")],
    )

    resolution = EdgeResolution(
        candidates=[candidate],
        decisions=[{"relationship_type": "none", "weight": 0.0, "justification": "No relationship"}],
        node_concept="concept_a",
    )

    ctx.graph_engine.record_negative_evaluations = AsyncMock(return_value=1)

    result = await EdgeResolver(ctx).apply_edge_decisions(resolution)

    assert result["edges_created"] == 0
    # Should have recorded the negative evaluation
    ctx.graph_engine.record_negative_evaluations.assert_called_once_with(
        source_id, target_id, [fact_id],
    )

async def test_resolve_edges_clears_evaluations_on_edge_creation() -> None:
    """apply_edge_decisions clears evaluations when an edge IS created."""
    from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver
    from kt_worker_nodes.pipelines.models import EdgeCandidate, EdgeResolution

    ctx = _make_ctx()
    fact_id = uuid.uuid4()
    source_id = uuid.uuid4()
    target_id = uuid.uuid4()

    candidate = EdgeCandidate(
        source_node_id=source_id,
        source_concept="quantum_computing",
        target_node_id=target_id,
        target_concept="quantum_mechanics",
        evidence_fact_ids=[fact_id],
        evidence_facts=[_make_mock_fact(fact_id=fact_id, content="QC uses QM")],
    )

    resolution = EdgeResolution(
        candidates=[candidate],
        decisions=[{
            "relationship_type": "related",
            "weight": 0.8,
            "justification": "QC is part of QM",
        }],
        node_concept="quantum_computing",
    )

    mock_edge = _make_mock_edge(source_id=source_id, target_id=target_id)
    ctx.graph_engine.create_edge = AsyncMock(return_value=mock_edge)
    ctx.graph_engine.link_fact_to_edge = AsyncMock()
    ctx.graph_engine.clear_evaluations_for_pair = AsyncMock(return_value=0)
    ctx.graph_engine.record_negative_evaluations = AsyncMock(return_value=0)

    result = await EdgeResolver(ctx).apply_edge_decisions(resolution)

    assert result["edges_created"] == 1
    # Should have cleared prior evaluations for this pair
    ctx.graph_engine.clear_evaluations_for_pair.assert_called_once_with(
        source_id, target_id,
    )
    # Should NOT have recorded negative evaluations
    ctx.graph_engine.record_negative_evaluations.assert_not_called()

async def test_resolve_edges_filters_previously_evaluated_facts() -> None:
    """discover_and_classify filters out facts that were previously evaluated negatively."""
    from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

    ctx = _make_ctx()
    node = _make_mock_node(concept="source_node")
    node.node_type = "concept"
    node.embedding = None

    other_id = uuid.uuid4()
    old_fact_id = uuid.uuid4()  # Previously evaluated, should be filtered
    new_fact_id = uuid.uuid4()  # New, should pass through

    # Mock candidate node for same-type filtering
    other_node = _make_mock_node(other_id, "other_node")
    other_node.node_type = "concept"
    ctx.graph_engine.get_nodes_by_ids = AsyncMock(return_value=[other_node])

    # Two shared facts, one previously evaluated
    ctx.graph_engine.find_nodes_sharing_facts = AsyncMock(
        return_value=[(other_id, "other_node", [old_fact_id, new_fact_id])]
    )
    ctx.graph_engine.get_recent_edge_pairs = AsyncMock(return_value=set())

    # Return the old fact as previously evaluated
    ctx.graph_engine.get_evaluated_fact_ids_batch = AsyncMock(
        return_value={other_id: {old_fact_id}},
    )

    # Mock fact loading — only new fact should be requested
    new_fact = _make_mock_fact(fact_id=new_fact_id, content="new evidence")
    ctx.graph_engine._fact_repo = AsyncMock()
    ctx.graph_engine._fact_repo.get_by_id = AsyncMock(return_value=new_fact)
    ctx.graph_engine.get_facts_by_ids = AsyncMock(return_value=[new_fact])

    # LLM classification
    ctx.model_gateway.generate_json = AsyncMock(return_value={
        "results": [{"relationship_type": "related", "weight": 0.6, "justification": "test"}],
    })

    resolution = await EdgeResolver(ctx).discover_and_classify(node)

    assert resolution is not None
    assert len(resolution.candidates) == 1
    candidate = resolution.candidates[0]
    # old_fact_id should have been filtered out
    assert old_fact_id not in candidate.evidence_fact_ids
    assert new_fact_id in candidate.evidence_fact_ids

async def test_resolve_edges_drops_candidate_when_all_facts_evaluated() -> None:
    """discover_and_classify drops candidates when all their facts were previously evaluated."""
    from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

    ctx = _make_ctx()
    node = _make_mock_node(concept="filtered_source")
    node.node_type = "concept"
    node.embedding = None

    other_id = uuid.uuid4()
    fact_id = uuid.uuid4()

    # Mock candidate node for same-type filtering
    other_node = _make_mock_node(other_id, "other_node")
    other_node.node_type = "concept"
    ctx.graph_engine.get_nodes_by_ids = AsyncMock(return_value=[other_node])

    ctx.graph_engine.find_nodes_sharing_facts = AsyncMock(
        return_value=[(other_id, "other_node", [fact_id])]
    )
    ctx.graph_engine.get_recent_edge_pairs = AsyncMock(return_value=set())

    # All facts for this candidate were already evaluated
    ctx.graph_engine.get_evaluated_fact_ids_batch = AsyncMock(
        return_value={other_id: {fact_id}},
    )

    resolution = await EdgeResolver(ctx).discover_and_classify(node)

    # No candidates left after filtering → None
    assert resolution is None

async def test_resolve_edges_negative_eval_records_below_min_weight() -> None:
    """Facts from edges rejected for low weight are recorded as negative evaluations."""
    from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver
    from kt_worker_nodes.pipelines.models import EdgeCandidate, EdgeResolution

    ctx = _make_ctx()
    fact_id = uuid.uuid4()
    source_id = uuid.uuid4()
    target_id = uuid.uuid4()

    candidate = EdgeCandidate(
        source_node_id=source_id,
        source_concept="weak_a",
        target_node_id=target_id,
        target_concept="weak_b",
        evidence_fact_ids=[fact_id],
        evidence_facts=[_make_mock_fact(fact_id=fact_id, content="weak evidence")],
    )

    resolution = EdgeResolution(
        candidates=[candidate],
        decisions=[{
            "relationship_type": "related",
            "weight": 0.1,
            "justification": "Very weak link",
        }],
        node_concept="weak_a",
    )

    # create_edge returns None (below min_edge_weight)
    ctx.graph_engine.create_edge = AsyncMock(return_value=None)
    ctx.graph_engine.record_negative_evaluations = AsyncMock(return_value=1)

    result = await EdgeResolver(ctx).apply_edge_decisions(resolution)

    assert result["edges_created"] == 0
    ctx.graph_engine.record_negative_evaluations.assert_called_once_with(
        source_id, target_id, [fact_id],
    )

async def test_classify_in_batches() -> None:
    """25 candidates split into 3 batches (10+10+5), all decisions collected."""
    from kt_worker_nodes.pipelines.edges.classifier import EdgeClassifier
    from kt_worker_nodes.pipelines.models import EdgeCandidate

    ctx = _make_ctx()

    # Create 25 candidates with facts
    candidates: list[EdgeCandidate] = []
    for i in range(25):
        fact = _make_mock_fact(fact_id=uuid.uuid4(), content=f"fact {i}")
        candidates.append(EdgeCandidate(
            source_node_id=uuid.uuid4(),
            source_concept=f"source_{i}",
            target_node_id=uuid.uuid4(),
            target_concept=f"target_{i}",
            evidence_fact_ids=[fact.id],
            evidence_facts=[fact],
        ))

    # LLM returns valid decisions for each batch
    call_count = {"n": 0}

    async def mock_generate_json(**kwargs: Any) -> dict[str, Any]:
        call_count["n"] += 1
        # Parse how many pairs are in this batch from the prompt
        prompt_text = kwargs["messages"][0]["content"]
        pair_count = prompt_text.count("--- Pair")
        return {
            "results": [
                {"relationship_type": "related", "weight": 0.6, "justification": f"batch {call_count['n']}"}
                for _ in range(pair_count)
            ]
        }

    ctx.model_gateway.generate_json = AsyncMock(side_effect=mock_generate_json)

    classifier = EdgeClassifier(ctx)
    decisions = await classifier.classify(candidates, batch_size=10)

    # 3 LLM calls: 10 + 10 + 5
    assert call_count["n"] == 3
    assert len(decisions) == 25
    # All should have valid decisions
    assert all(d is not None for d in decisions)
    assert all(d["relationship_type"] == "related" for d in decisions if d is not None)

# ── resolve_edges: same-type filtering tests ─────────────────────

async def test_resolve_edges_same_type_filtering() -> None:
    """Only same-type candidates pass the same-type filter."""
    from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

    ctx = _make_ctx()
    node = _make_mock_node(concept="quantum_computing")
    node.node_type = "concept"

    same_type_id = uuid.uuid4()
    diff_type_id = uuid.uuid4()
    fact_id_1 = uuid.uuid4()
    fact_id_2 = uuid.uuid4()

    # Two candidates: one concept, one perspective
    ctx.graph_engine.find_nodes_sharing_facts = AsyncMock(
        return_value=[
            (same_type_id, "quantum_mechanics", [fact_id_1]),
            (diff_type_id, "quantum_is_great", [fact_id_2]),
        ]
    )

    # Only the concept node passes same-type filtering
    same_node = _make_mock_node(same_type_id, "quantum_mechanics")
    same_node.node_type = "concept"
    diff_node = _make_mock_node(diff_type_id, "quantum_is_great")
    diff_node.node_type = "perspective"
    ctx.graph_engine.get_nodes_by_ids = AsyncMock(return_value=[same_node, diff_node])

    ctx.graph_engine.get_recent_edge_pairs = AsyncMock(return_value=set())
    ctx.graph_engine.get_evaluated_fact_ids_batch = AsyncMock(return_value={})

    mock_fact = _make_mock_fact(fact_id=fact_id_1, content="Quantum computing relies on QM")
    ctx.graph_engine._fact_repo = AsyncMock()
    ctx.graph_engine._fact_repo.get_by_id = AsyncMock(return_value=mock_fact)
    ctx.graph_engine.get_facts_by_ids = AsyncMock(return_value=[mock_fact])

    ctx.model_gateway.generate_json = AsyncMock(return_value={
        "results": [{"relationship_type": "related", "weight": 0.6, "justification": "test"}]
    })

    resolution = await EdgeResolver(ctx).discover_and_classify(node)

    assert resolution is not None
    assert len(resolution.candidates) == 1
    assert resolution.candidates[0].target_node_id == same_type_id

async def test_resolve_edges_shared_fact_discovery_only() -> None:
    """Shared-fact candidate is discovered and classified correctly."""
    from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

    ctx = _make_ctx()
    node = _make_mock_node(concept="no_dims_node")
    node.node_type = "concept"

    other_id = uuid.uuid4()
    fact_id = uuid.uuid4()

    # Mock candidate node for same-type filtering
    other_node = _make_mock_node(other_id, "other")
    other_node.node_type = "concept"
    ctx.graph_engine.get_nodes_by_ids = AsyncMock(return_value=[other_node])

    # Shared-fact candidate exists
    ctx.graph_engine.find_nodes_sharing_facts = AsyncMock(
        return_value=[(other_id, "other", [fact_id])]
    )

    ctx.graph_engine.get_recent_edge_pairs = AsyncMock(return_value=set())
    ctx.graph_engine.get_evaluated_fact_ids_batch = AsyncMock(return_value={})

    mock_fact = _make_mock_fact(fact_id=fact_id, content="a fact")
    ctx.graph_engine._fact_repo = AsyncMock()
    ctx.graph_engine._fact_repo.get_by_id = AsyncMock(return_value=mock_fact)
    ctx.graph_engine.get_facts_by_ids = AsyncMock(return_value=[mock_fact])

    ctx.model_gateway.generate_json = AsyncMock(return_value={
        "results": [{"relationship_type": "related", "weight": 0.6, "justification": "test"}]
    })

    resolution = await EdgeResolver(ctx).discover_and_classify(node)

    assert resolution is not None
    assert len(resolution.candidates) == 1
    assert resolution.candidates[0].target_node_id == other_id

async def test_resolve_edges_multiple_candidates() -> None:
    """Multiple shared-fact candidates are all included in resolution."""
    from kt_worker_nodes.pipelines.edges.resolver import EdgeResolver

    ctx = _make_ctx()
    node = _make_mock_node(concept="photosynthesis")
    node.node_type = "concept"

    id_a = uuid.uuid4()
    id_b = uuid.uuid4()
    fid_a = uuid.uuid4()
    fid_b = uuid.uuid4()

    # Mock candidate nodes for same-type filtering
    node_a = _make_mock_node(id_a, "chloroplast")
    node_a.node_type = "concept"
    node_b = _make_mock_node(id_b, "sunlight")
    node_b.node_type = "concept"
    ctx.graph_engine.get_nodes_by_ids = AsyncMock(return_value=[node_a, node_b])

    # Two candidates with shared facts
    ctx.graph_engine.find_nodes_sharing_facts = AsyncMock(
        return_value=[
            (id_a, "chloroplast", [fid_a]),
            (id_b, "sunlight", [fid_b]),
        ]
    )

    ctx.graph_engine.get_recent_edge_pairs = AsyncMock(return_value=set())
    ctx.graph_engine.get_evaluated_fact_ids_batch = AsyncMock(return_value={})

    fact_a = _make_mock_fact(fact_id=fid_a, content="chloroplast fact")
    fact_b = _make_mock_fact(fact_id=fid_b, content="sunlight fact")
    ctx.graph_engine._fact_repo = AsyncMock()
    ctx.graph_engine._fact_repo.get_by_id = AsyncMock(
        side_effect=lambda fid: fact_a if fid == fid_a else fact_b
    )
    ctx.graph_engine.get_facts_by_ids = AsyncMock(return_value=[fact_a, fact_b])

    ctx.model_gateway.generate_json = AsyncMock(return_value={
        "results": [
            {"relationship_type": "related", "weight": 0.8, "justification": "test a"},
            {"relationship_type": "related", "weight": 0.6, "justification": "test b"},
        ]
    })

    resolution = await EdgeResolver(ctx).discover_and_classify(node)

    assert resolution is not None
    assert len(resolution.candidates) == 2
    candidate_ids = {c.target_node_id for c in resolution.candidates}
    assert id_a in candidate_ids
    assert id_b in candidate_ids
