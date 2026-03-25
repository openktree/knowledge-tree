"""Unit tests for orchestrator agent tools with mocked dependencies."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from kt_agents_core.state import AgentContext, PipelineState
from kt_agents_core.synthesis import _extract_text_content, synthesize_answer_impl

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
    """Create a PipelineState with defaults."""
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
