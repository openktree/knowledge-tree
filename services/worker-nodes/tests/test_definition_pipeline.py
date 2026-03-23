"""Unit tests for definition pipeline."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from kt_worker_nodes.pipelines.definitions.pipeline import DefinitionPipeline


def _make_dimension(content="Test content", is_definitive=False, fact_count=10, model_id="test-model"):
    """Create a mock Dimension."""
    dim = MagicMock()
    dim.id = uuid4()
    dim.content = content
    dim.is_definitive = is_definitive
    dim.fact_count = fact_count
    dim.model_id = model_id
    dim.batch_index = 0
    return dim


def _make_ctx(dimensions=None, definition_model="test-model", generate_response="A test definition."):
    """Create a mock AgentContext."""
    ctx = MagicMock()

    # graph_engine
    ctx.graph_engine = MagicMock()
    mock_node = MagicMock()
    mock_node.metadata_ = None
    mock_node.definition = None
    ctx.graph_engine.get_node = AsyncMock(return_value=mock_node)
    ctx.graph_engine.get_dimensions = AsyncMock(return_value=dimensions or [])
    ctx.graph_engine.set_node_definition = AsyncMock()

    # model_gateway
    ctx.model_gateway = MagicMock()
    ctx.model_gateway.definition_model = definition_model
    ctx.model_gateway.definition_thinking_level = ""
    ctx.model_gateway.generate = AsyncMock(return_value=generate_response)

    return ctx


class TestDefinitionPipeline:
    """Tests for DefinitionPipeline."""

    @pytest.mark.asyncio
    async def test_no_dimensions_returns_none(self) -> None:
        ctx = _make_ctx(dimensions=[])
        pipeline = DefinitionPipeline(ctx)
        result = await pipeline.generate_definition(uuid4(), "Test Concept")
        assert result is None
        ctx.model_gateway.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_generates_definition_from_dimensions(self) -> None:
        dims = [
            _make_dimension("Dimension 1 content", is_definitive=True, fact_count=50),
            _make_dimension("Dimension 2 content", is_definitive=False, fact_count=10),
        ]
        ctx = _make_ctx(dimensions=dims, generate_response="Synthesized definition text.")
        pipeline = DefinitionPipeline(ctx)
        node_id = uuid4()
        result = await pipeline.generate_definition(node_id, "Test Concept")

        assert result == "Synthesized definition text."
        ctx.model_gateway.generate.assert_called_once()
        ctx.graph_engine.set_node_definition.assert_called_once_with(node_id, "Synthesized definition text.")

    @pytest.mark.asyncio
    async def test_prompt_includes_definitive_markers(self) -> None:
        dims = [
            _make_dimension("Content A", is_definitive=True, fact_count=50),
            _make_dimension("Content B", is_definitive=False, fact_count=10),
        ]
        ctx = _make_ctx(dimensions=dims)
        pipeline = DefinitionPipeline(ctx)
        await pipeline.generate_definition(uuid4(), "Test Concept")

        # Check the user message passed to generate
        call_args = ctx.model_gateway.generate.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages") or call_args[0][1]
        user_msg = messages[0]["content"]
        assert "[DEFINITIVE]" in user_msg
        assert "[DRAFT" in user_msg

    @pytest.mark.asyncio
    async def test_empty_response_returns_none(self) -> None:
        dims = [_make_dimension()]
        ctx = _make_ctx(dimensions=dims, generate_response="   ")
        pipeline = DefinitionPipeline(ctx)
        result = await pipeline.generate_definition(uuid4(), "Test Concept")
        assert result is None
        ctx.graph_engine.set_node_definition.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_error_returns_none(self) -> None:
        dims = [_make_dimension()]
        ctx = _make_ctx(dimensions=dims)
        ctx.model_gateway.generate = AsyncMock(side_effect=Exception("LLM error"))
        pipeline = DefinitionPipeline(ctx)
        result = await pipeline.generate_definition(uuid4(), "Test Concept")
        assert result is None
