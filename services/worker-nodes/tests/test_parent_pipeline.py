"""Tests for LLM-based parent selection pipeline."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from kt_agents_core.state import AgentContext
from kt_config.types import DEFAULT_PARENTS
from kt_worker_nodes.pipelines.nodes.types import CreateNodeTask
from kt_worker_nodes.pipelines.parent.pipeline import ParentSelectionPipeline

# ── Helpers ───────────────────────────────────────────────────────────


def _make_ctx() -> AgentContext:
    """Create an AgentContext with all-mock dependencies."""
    graph_engine = AsyncMock()
    model_gateway = MagicMock()
    model_gateway.parent_selection_model = "test-model"
    model_gateway.parent_selection_thinking_level = ""
    model_gateway.generate_json = AsyncMock()

    graph_engine._node_repo = AsyncMock()

    session = AsyncMock()
    nested_cm = AsyncMock()
    nested_cm.__aenter__ = AsyncMock(return_value=None)
    nested_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin_nested = MagicMock(return_value=nested_cm)

    return AgentContext(
        graph_engine=graph_engine,
        provider_registry=AsyncMock(),
        model_gateway=model_gateway,
        embedding_service=AsyncMock(),
        session=session,
    )


def _make_node(
    node_id: uuid.UUID | None = None,
    concept: str = "test_concept",
    node_type: str = "concept",
    parent_id: uuid.UUID | None = None,
    access_count: int = 0,
) -> MagicMock:
    """Create a mock Node object."""
    node = MagicMock()
    node.id = node_id or uuid.uuid4()
    node.concept = concept
    node.node_type = node_type
    node.parent_id = parent_id
    node.access_count = access_count
    return node


def _make_edge(
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    weight: float = 0.5,
) -> MagicMock:
    """Create a mock Edge object."""
    edge = MagicMock()
    edge.source_node_id = source_id
    edge.target_node_id = target_id
    edge.weight = weight
    return edge


def _make_dim(content: str = "A dimension description.") -> MagicMock:
    """Create a mock Dimension object."""
    dim = MagicMock()
    dim.content = content
    return dim


def _make_task(
    node: MagicMock,
    node_type: str = "concept",
    action: str = "create",
) -> CreateNodeTask:
    """Create a CreateNodeTask with a mock node."""
    task = CreateNodeTask(name=node.concept, node_type=node_type, seed_key=f"{node_type}:{node.concept}")
    task.action = action
    task.node = node
    return task


# ── Tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_entities_are_skipped() -> None:
    """Entity nodes should be skipped entirely — no parent assignment."""
    ctx = _make_ctx()
    node = _make_node(concept="OpenAI", node_type="entity")
    task = _make_task(node, node_type="entity")
    pipeline = ParentSelectionPipeline(ctx)

    await pipeline.select_parents_batch([task])

    ctx.graph_engine.set_parent.assert_not_called()


@pytest.mark.asyncio
async def test_no_edges_assigns_default_parent() -> None:
    """Node with no edges gets the default parent for its type."""
    ctx = _make_ctx()
    node = _make_node(concept="transistor")
    task = _make_task(node)

    ctx.graph_engine.get_edges.return_value = []

    pipeline = ParentSelectionPipeline(ctx)
    await pipeline.select_parents_batch([task])

    ctx.graph_engine.set_parent.assert_awaited_once_with(
        node.id,
        DEFAULT_PARENTS["concept"],
    )


@pytest.mark.asyncio
async def test_no_same_type_candidates_assigns_default() -> None:
    """If all neighbors are a different type, assign default parent."""
    ctx = _make_ctx()
    node = _make_node(concept="transistor")
    task = _make_task(node)

    other_id = uuid.uuid4()
    ctx.graph_engine.get_edges.return_value = [
        _make_edge(node.id, other_id, weight=0.8),
    ]
    # Neighbor is an entity, not a concept
    neighbor = _make_node(node_id=other_id, concept="Intel", node_type="entity")
    ctx.graph_engine.get_nodes_by_ids.return_value = [neighbor]

    pipeline = ParentSelectionPipeline(ctx)
    await pipeline.select_parents_batch([task])

    ctx.graph_engine.set_parent.assert_awaited_once_with(
        node.id,
        DEFAULT_PARENTS["concept"],
    )


@pytest.mark.asyncio
async def test_valid_llm_choice_sets_parent() -> None:
    """LLM picks candidate 1 → node's parent is set to that candidate."""
    ctx = _make_ctx()
    node = _make_node(concept="transistor")
    task = _make_task(node)

    candidate = _make_node(concept="microchip")
    ctx.graph_engine.get_edges.return_value = [
        _make_edge(node.id, candidate.id, weight=0.8),
    ]
    ctx.graph_engine.get_nodes_by_ids.return_value = [candidate]
    ctx.graph_engine.get_dimensions.return_value = [
        _make_dim("A semiconductor device used to amplify signals."),
    ]

    ctx.model_gateway.generate_json.return_value = {"choice": 1}

    pipeline = ParentSelectionPipeline(ctx)
    await pipeline.select_parents_batch([task])

    ctx.graph_engine.set_parent.assert_awaited_once_with(node.id, candidate.id)


@pytest.mark.asyncio
async def test_null_choice_assigns_default() -> None:
    """LLM returns choice=null → assign default parent."""
    ctx = _make_ctx()
    node = _make_node(concept="transistor")
    task = _make_task(node)

    candidate = _make_node(concept="silicon wafer")
    ctx.graph_engine.get_edges.return_value = [
        _make_edge(node.id, candidate.id, weight=0.6),
    ]
    ctx.graph_engine.get_nodes_by_ids.return_value = [candidate]
    ctx.graph_engine.get_dimensions.return_value = []

    ctx.model_gateway.generate_json.return_value = {"choice": None}

    pipeline = ParentSelectionPipeline(ctx)
    await pipeline.select_parents_batch([task])

    ctx.graph_engine.set_parent.assert_awaited_once_with(
        node.id,
        DEFAULT_PARENTS["concept"],
    )


@pytest.mark.asyncio
async def test_out_of_range_choice_assigns_default() -> None:
    """LLM returns a choice number beyond the candidate list → default."""
    ctx = _make_ctx()
    node = _make_node(concept="transistor")
    task = _make_task(node)

    candidate = _make_node(concept="microchip")
    ctx.graph_engine.get_edges.return_value = [
        _make_edge(node.id, candidate.id, weight=0.7),
    ]
    ctx.graph_engine.get_nodes_by_ids.return_value = [candidate]
    ctx.graph_engine.get_dimensions.return_value = []

    ctx.model_gateway.generate_json.return_value = {"choice": 5}

    pipeline = ParentSelectionPipeline(ctx)
    await pipeline.select_parents_batch([task])

    ctx.graph_engine.set_parent.assert_awaited_once_with(
        node.id,
        DEFAULT_PARENTS["concept"],
    )


@pytest.mark.asyncio
async def test_prompt_includes_dimensions() -> None:
    """The user prompt sent to the LLM should include node dimensions."""
    ctx = _make_ctx()
    node = _make_node(concept="transistor")
    task = _make_task(node)

    candidate = _make_node(concept="microchip")
    ctx.graph_engine.get_edges.return_value = [
        _make_edge(node.id, candidate.id, weight=0.8),
    ]
    ctx.graph_engine.get_nodes_by_ids.return_value = [candidate]
    ctx.graph_engine.get_dimensions.return_value = [
        _make_dim("A semiconductor device used to amplify or switch signals."),
    ]

    ctx.model_gateway.generate_json.return_value = {"choice": 1}

    pipeline = ParentSelectionPipeline(ctx)
    await pipeline.select_parents_batch([task])

    call_args = ctx.model_gateway.generate_json.call_args
    user_content = call_args.kwargs["messages"][0]["content"]
    assert "transistor" in user_content
    assert "semiconductor device" in user_content
    assert "microchip" in user_content


@pytest.mark.asyncio
async def test_prompt_includes_parent_context() -> None:
    """Candidates and node should show their current parent names."""
    ctx = _make_ctx()

    parent_node = _make_node(concept="electronic components")
    node = _make_node(concept="transistor", parent_id=parent_node.id)
    task = _make_task(node)

    cand_parent = _make_node(concept="semiconductor manufacturing")
    candidate = _make_node(concept="microchip", parent_id=cand_parent.id)

    ctx.graph_engine.get_edges.return_value = [
        _make_edge(node.id, candidate.id, weight=0.8),
    ]
    # First call: get candidates; second call: resolve parent names
    ctx.graph_engine.get_nodes_by_ids.side_effect = [
        [candidate],
        [parent_node, cand_parent],
    ]
    ctx.graph_engine.get_dimensions.return_value = [
        _make_dim("A semiconductor device."),
    ]

    ctx.model_gateway.generate_json.return_value = {"choice": 1}

    pipeline = ParentSelectionPipeline(ctx)
    await pipeline.select_parents_batch([task])

    call_args = ctx.model_gateway.generate_json.call_args
    user_content = call_args.kwargs["messages"][0]["content"]
    assert "electronic components" in user_content
    assert "semiconductor manufacturing" in user_content


@pytest.mark.asyncio
async def test_reversal_swaps_parent_child() -> None:
    """If chosen candidate's parent_id == node.id, swap the relationship."""
    ctx = _make_ctx()

    # node is currently parent of chosen_candidate
    node = _make_node(
        concept="electronics",
        parent_id=DEFAULT_PARENTS["concept"],
    )
    chosen = _make_node(concept="microchip", parent_id=node.id)

    task = _make_task(node)

    ctx.graph_engine.get_edges.return_value = [
        _make_edge(node.id, chosen.id, weight=0.9),
    ]
    ctx.graph_engine.get_nodes_by_ids.return_value = [chosen]
    ctx.graph_engine.get_dimensions.return_value = []
    ctx.model_gateway.generate_json.return_value = {"choice": 1}

    pipeline = ParentSelectionPipeline(ctx)
    await pipeline.select_parents_batch([task])

    # Chosen should be promoted to node's old parent (default)
    # Node should become child of chosen
    calls = ctx.graph_engine.set_parent.call_args_list
    assert len(calls) == 2
    # First call: promote chosen to node's old parent
    assert calls[0].args == (chosen.id, DEFAULT_PARENTS["concept"])
    # Second call: node becomes child of chosen
    assert calls[1].args == (node.id, chosen.id)


@pytest.mark.asyncio
async def test_no_reversal_when_not_child() -> None:
    """Normal assignment (no reversal) when chosen is not a child of node."""
    ctx = _make_ctx()
    node = _make_node(concept="transistor")
    candidate = _make_node(concept="microchip", parent_id=uuid.uuid4())
    task = _make_task(node)

    ctx.graph_engine.get_edges.return_value = [
        _make_edge(node.id, candidate.id, weight=0.8),
    ]
    ctx.graph_engine.get_nodes_by_ids.return_value = [candidate]
    ctx.graph_engine.get_dimensions.return_value = []
    ctx.model_gateway.generate_json.return_value = {"choice": 1}

    pipeline = ParentSelectionPipeline(ctx)
    await pipeline.select_parents_batch([task])

    ctx.graph_engine.set_parent.assert_awaited_once_with(node.id, candidate.id)


@pytest.mark.asyncio
async def test_always_reevaluates_existing_parent() -> None:
    """Nodes with existing non-default parents should still be re-evaluated."""
    ctx = _make_ctx()
    existing_parent_id = uuid.uuid4()
    node = _make_node(concept="transistor", parent_id=existing_parent_id)
    task = _make_task(node)

    candidate = _make_node(concept="microchip")
    ctx.graph_engine.get_edges.return_value = [
        _make_edge(node.id, candidate.id, weight=0.7),
    ]
    ctx.graph_engine.get_nodes_by_ids.return_value = [candidate]
    ctx.graph_engine.get_dimensions.return_value = []
    ctx.model_gateway.generate_json.return_value = {"choice": 1}

    pipeline = ParentSelectionPipeline(ctx)
    await pipeline.select_parents_batch([task])

    # Should still call the LLM and set parent — not skip
    ctx.model_gateway.generate_json.assert_awaited_once()
    ctx.graph_engine.set_parent.assert_awaited_once_with(node.id, candidate.id)


@pytest.mark.asyncio
async def test_dimension_truncation() -> None:
    """Long dimension content should be truncated at ~300 chars."""
    ctx = _make_ctx()
    node = _make_node(concept="transistor")
    task = _make_task(node)

    candidate = _make_node(concept="microchip")
    ctx.graph_engine.get_edges.return_value = [
        _make_edge(node.id, candidate.id, weight=0.8),
    ]
    ctx.graph_engine.get_nodes_by_ids.return_value = [candidate]

    long_content = "x" * 500
    ctx.graph_engine.get_dimensions.return_value = [_make_dim(long_content)]
    ctx.model_gateway.generate_json.return_value = {"choice": 1}

    pipeline = ParentSelectionPipeline(ctx)
    await pipeline.select_parents_batch([task])

    call_args = ctx.model_gateway.generate_json.call_args
    user_content = call_args.kwargs["messages"][0]["content"]
    # Should be truncated — content should not contain all 500 x's
    assert "x" * 500 not in user_content
    assert "..." in user_content


@pytest.mark.asyncio
async def test_skip_action_is_ignored() -> None:
    """Tasks with action='skip' should not trigger parent selection."""
    ctx = _make_ctx()
    node = _make_node(concept="transistor")
    task = _make_task(node, action="skip")

    pipeline = ParentSelectionPipeline(ctx)
    await pipeline.select_parents_batch([task])

    ctx.graph_engine.get_edges.assert_not_called()
    ctx.graph_engine.set_parent.assert_not_called()


@pytest.mark.asyncio
async def test_system_prompt_contains_type_instruction() -> None:
    """System prompt should contain the correct type instruction."""
    ctx = _make_ctx()
    node = _make_node(concept="D-Day landings", node_type="event")
    task = _make_task(node, node_type="event")

    candidate = _make_node(
        concept="Battle of Normandy",
        node_type="event",
    )
    ctx.graph_engine.get_edges.return_value = [
        _make_edge(node.id, candidate.id, weight=0.8),
    ]
    ctx.graph_engine.get_nodes_by_ids.return_value = [candidate]
    ctx.graph_engine.get_dimensions.return_value = []
    ctx.model_gateway.generate_json.return_value = {"choice": 1}

    pipeline = ParentSelectionPipeline(ctx)
    await pipeline.select_parents_batch([task])

    call_args = ctx.model_gateway.generate_json.call_args
    system = call_args.kwargs["system_prompt"]
    assert "immediate larger event" in system
