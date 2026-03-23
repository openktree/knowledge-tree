"""Unit tests for ontology crystallization helpers and pipeline logic."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kt_ontology.crystallization import (
    CrystallizationPipeline,
    _is_crystallized,
    _needs_recrystallization,
)
from kt_worker_nodes.prompts.crystallization import (
    CRYSTALLIZATION_SYSTEM_PROMPT,
    build_crystallization_user_prompt,
)

# ── Helper factories ─────────────────────────────────────────────


def _make_node(
    metadata: dict | None = None,
    definition: str | None = None,
    updated_at: datetime | None = None,
    concept: str = "test concept",
    node_type: str = "concept",
) -> MagicMock:
    """Create a mock Node with the given metadata."""
    node = MagicMock()
    node.id = uuid.uuid4()
    node.concept = concept
    node.node_type = node_type
    node.metadata_ = metadata
    node.definition = definition
    node.updated_at = updated_at or datetime.now(timezone.utc)
    return node


def _make_child(
    concept: str = "child",
    updated_at: datetime | None = None,
    definition: str | None = None,
    node_type: str = "concept",
) -> MagicMock:
    """Create a mock child Node."""
    child = MagicMock()
    child.id = uuid.uuid4()
    child.concept = concept
    child.node_type = node_type
    child.definition = definition or f"Definition of {concept}"
    child.updated_at = updated_at or datetime.now(timezone.utc)
    return child


# ══════════════════════════════════════════════════════════════════
# _is_crystallized tests
# ══════════════════════════════════════════════════════════════════


class TestIsCrystallized:
    def test_none_metadata(self) -> None:
        node = _make_node(metadata=None)
        assert _is_crystallized(node) is False

    def test_empty_metadata(self) -> None:
        node = _make_node(metadata={})
        assert _is_crystallized(node) is False

    def test_not_stable(self) -> None:
        node = _make_node(metadata={"ontology_stable": False})
        assert _is_crystallized(node) is False

    def test_stable(self) -> None:
        node = _make_node(metadata={"ontology_stable": True})
        assert _is_crystallized(node) is True

    def test_truthy_value(self) -> None:
        node = _make_node(metadata={"ontology_stable": 1})
        assert _is_crystallized(node) is True

    def test_missing_key(self) -> None:
        node = _make_node(metadata={"other_key": "value"})
        assert _is_crystallized(node) is False


# ══════════════════════════════════════════════════════════════════
# _needs_recrystallization tests
# ══════════════════════════════════════════════════════════════════


class TestNeedsRecrystallization:
    def test_not_crystallized(self) -> None:
        node = _make_node(metadata={})
        children = [_make_child() for _ in range(5)]
        assert _needs_recrystallization(node, children) is False

    def test_missing_crystallized_at(self) -> None:
        node = _make_node(metadata={"ontology_stable": True})
        children = [_make_child() for _ in range(5)]
        assert _needs_recrystallization(node, children) is True

    def test_bad_timestamp(self) -> None:
        node = _make_node(metadata={"ontology_stable": True, "crystallized_at": "not-a-date"})
        children = [_make_child() for _ in range(5)]
        assert _needs_recrystallization(node, children) is True

    def test_no_children_changed(self) -> None:
        crystallized_at = datetime.now(timezone.utc)
        node = _make_node(
            metadata={
                "ontology_stable": True,
                "crystallized_at": crystallized_at.isoformat(),
            }
        )
        # Children updated BEFORE crystallization
        old_time = crystallized_at - timedelta(hours=1)
        children = [_make_child(updated_at=old_time) for _ in range(10)]
        assert _needs_recrystallization(node, children) is False

    def test_all_children_changed(self) -> None:
        crystallized_at = datetime.now(timezone.utc) - timedelta(hours=2)
        node = _make_node(
            metadata={
                "ontology_stable": True,
                "crystallized_at": crystallized_at.isoformat(),
            }
        )
        # Children updated AFTER crystallization
        new_time = datetime.now(timezone.utc)
        children = [_make_child(updated_at=new_time) for _ in range(10)]
        assert _needs_recrystallization(node, children) is True

    def test_exactly_at_threshold(self) -> None:
        """50% changed exactly at threshold (> not >=)."""
        crystallized_at = datetime.now(timezone.utc) - timedelta(hours=2)
        node = _make_node(
            metadata={
                "ontology_stable": True,
                "crystallized_at": crystallized_at.isoformat(),
            }
        )
        old_time = crystallized_at - timedelta(hours=1)
        new_time = datetime.now(timezone.utc)
        children = [_make_child(updated_at=new_time) for _ in range(5)]
        children += [_make_child(updated_at=old_time) for _ in range(5)]
        # 50% changed — should NOT trigger (> 0.5, not >=)
        assert _needs_recrystallization(node, children, child_change_ratio=0.5) is False

    def test_above_threshold(self) -> None:
        """51% changed — should trigger."""
        crystallized_at = datetime.now(timezone.utc) - timedelta(hours=2)
        node = _make_node(
            metadata={
                "ontology_stable": True,
                "crystallized_at": crystallized_at.isoformat(),
            }
        )
        old_time = crystallized_at - timedelta(hours=1)
        new_time = datetime.now(timezone.utc)
        # 6 of 10 changed = 60% > 50%
        children = [_make_child(updated_at=new_time) for _ in range(6)]
        children += [_make_child(updated_at=old_time) for _ in range(4)]
        assert _needs_recrystallization(node, children, child_change_ratio=0.5) is True

    def test_empty_children(self) -> None:
        node = _make_node(
            metadata={
                "ontology_stable": True,
                "crystallized_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        assert _needs_recrystallization(node, []) is False

    def test_custom_ratio(self) -> None:
        crystallized_at = datetime.now(timezone.utc) - timedelta(hours=2)
        node = _make_node(
            metadata={
                "ontology_stable": True,
                "crystallized_at": crystallized_at.isoformat(),
            }
        )
        new_time = datetime.now(timezone.utc)
        old_time = crystallized_at - timedelta(hours=1)
        # 3 of 10 changed = 30%, threshold 0.25
        children = [_make_child(updated_at=new_time) for _ in range(3)]
        children += [_make_child(updated_at=old_time) for _ in range(7)]
        assert _needs_recrystallization(node, children, child_change_ratio=0.25) is True


# ══════════════════════════════════════════════════════════════════
# build_crystallization_user_prompt tests
# ══════════════════════════════════════════════════════════════════


class TestBuildCrystallizationUserPrompt:
    def test_basic(self) -> None:
        result = build_crystallization_user_prompt(
            parent_concept="Machine Learning",
            parent_definition="A field of AI",
            dimensions=[],
            children=[],
        )
        assert "Category: Machine Learning" in result
        assert "A field of AI" in result

    def test_no_definition(self) -> None:
        result = build_crystallization_user_prompt(
            parent_concept="Test",
            parent_definition=None,
            dimensions=[],
            children=[],
        )
        assert "Current definition" not in result

    def test_with_dimensions(self) -> None:
        dims = [
            {"model_id": "model-a", "content": "Analysis A"},
            {"model_id": "model-b", "content": "Analysis B"},
        ]
        result = build_crystallization_user_prompt(
            parent_concept="Test",
            parent_definition=None,
            dimensions=dims,
            children=[],
        )
        assert "model-a" in result
        assert "Analysis A" in result

    def test_with_children(self) -> None:
        children = [
            {"concept": "Child1", "definition": "Def1", "node_type": "concept"},
            {"concept": "Child2", "definition": "Def2", "node_type": "perspective"},
        ]
        result = build_crystallization_user_prompt(
            parent_concept="Test",
            parent_definition=None,
            dimensions=[],
            children=children,
        )
        assert "Child1" in result
        assert "[perspective] Child2" in result

    def test_children_capped_at_50(self) -> None:
        children = [{"concept": f"Child{i}", "definition": f"Def{i}", "node_type": "concept"} for i in range(60)]
        result = build_crystallization_user_prompt(
            parent_concept="Test",
            parent_definition=None,
            dimensions=[],
            children=children,
        )
        assert "and 10 more children" in result

    def test_with_perspectives(self) -> None:
        result = build_crystallization_user_prompt(
            parent_concept="Test",
            parent_definition=None,
            dimensions=[],
            children=[],
            child_perspectives=[("child1", "perspective1"), ("child2", "perspective2")],
        )
        assert "child1 → perspective1" in result

    def test_perspectives_capped_at_30(self) -> None:
        perspectives = [(f"child{i}", f"persp{i}") for i in range(40)]
        result = build_crystallization_user_prompt(
            parent_concept="Test",
            parent_definition=None,
            dimensions=[],
            children=[],
            child_perspectives=perspectives,
        )
        assert "and 10 more perspectives" in result


# ══════════════════════════════════════════════════════════════════
# CrystallizationPipeline tests
# ══════════════════════════════════════════════════════════════════


def _make_ctx(
    node: MagicMock | None = None,
    children: list | None = None,
    dimensions: list | None = None,
    perspectives: list | None = None,
    generate_result: str = "A crystallized definition.",
) -> MagicMock:
    """Build a mock AgentContext for CrystallizationPipeline."""
    ctx = MagicMock()
    ctx.graph_engine = MagicMock()
    ctx.graph_engine.get_node = AsyncMock(return_value=node)
    ctx.graph_engine.get_children = AsyncMock(return_value=children or [])
    ctx.graph_engine.count_children = AsyncMock(return_value=len(children or []))
    ctx.graph_engine.get_dimensions = AsyncMock(return_value=dimensions or [])
    ctx.graph_engine.get_perspectives = AsyncMock(return_value=perspectives or [])
    ctx.graph_engine.set_node_definition = AsyncMock()
    ctx.graph_engine.update_node = AsyncMock()

    ctx.model_gateway = MagicMock()
    ctx.model_gateway.crystallization_model = "test-model"
    ctx.model_gateway.crystallization_thinking_level = ""
    ctx.model_gateway.generate = AsyncMock(return_value=generate_result)

    return ctx


@pytest.mark.asyncio
class TestCrystallizationPipeline:
    async def test_node_not_found(self) -> None:
        ctx = _make_ctx(node=None)
        pipeline = CrystallizationPipeline(ctx)
        result = await pipeline.check_and_crystallize(uuid.uuid4())
        assert result is False

    @patch("kt_ontology.crystallization.get_settings")
    async def test_below_threshold(self, mock_settings: MagicMock) -> None:
        mock_settings.return_value.crystallization_child_threshold = 10
        mock_settings.return_value.crystallization_child_change_ratio = 0.5

        node = _make_node(concept="Test", metadata={})
        children = [_make_child() for _ in range(5)]
        ctx = _make_ctx(node=node, children=children)

        pipeline = CrystallizationPipeline(ctx)
        result = await pipeline.check_and_crystallize(node.id)
        assert result is False
        ctx.model_gateway.generate.assert_not_awaited()

    @patch("kt_ontology.crystallization.get_settings")
    async def test_crystallizes_at_threshold(self, mock_settings: MagicMock) -> None:
        mock_settings.return_value.crystallization_child_threshold = 10
        mock_settings.return_value.crystallization_child_change_ratio = 0.5

        node = _make_node(concept="Machine Learning", metadata={})
        children = [_make_child(concept=f"child-{i}") for i in range(10)]
        ctx = _make_ctx(node=node, children=children, generate_result="Crystallized def.")

        pipeline = CrystallizationPipeline(ctx)
        result = await pipeline.check_and_crystallize(node.id)

        assert result is True
        ctx.graph_engine.set_node_definition.assert_awaited_once()
        ctx.graph_engine.update_node.assert_awaited_once()

        # Check metadata was set correctly
        call_kwargs = ctx.graph_engine.update_node.call_args
        metadata = call_kwargs.kwargs.get("metadata_") or call_kwargs[1].get("metadata_")
        assert metadata["ontology_stable"] is True
        assert "crystallized_at" in metadata
        assert metadata["crystallized_child_count"] == 10

    @patch("kt_ontology.crystallization.get_settings")
    async def test_already_crystallized_no_change(self, mock_settings: MagicMock) -> None:
        mock_settings.return_value.crystallization_child_threshold = 10
        mock_settings.return_value.crystallization_child_change_ratio = 0.5

        crystallized_at = datetime.now(timezone.utc)
        node = _make_node(
            concept="ML",
            metadata={
                "ontology_stable": True,
                "crystallized_at": crystallized_at.isoformat(),
                "crystallized_child_count": 10,
            },
        )
        # Children updated BEFORE crystallization
        old_time = crystallized_at - timedelta(hours=1)
        children = [_make_child(updated_at=old_time) for _ in range(10)]
        ctx = _make_ctx(node=node, children=children)

        pipeline = CrystallizationPipeline(ctx)
        result = await pipeline.check_and_crystallize(node.id)

        assert result is False
        ctx.model_gateway.generate.assert_not_awaited()

    @patch("kt_ontology.crystallization.get_settings")
    async def test_recrystallizes_on_child_change(self, mock_settings: MagicMock) -> None:
        mock_settings.return_value.crystallization_child_threshold = 10
        mock_settings.return_value.crystallization_child_change_ratio = 0.5

        crystallized_at = datetime.now(timezone.utc) - timedelta(hours=2)
        node = _make_node(
            concept="ML",
            metadata={
                "ontology_stable": True,
                "crystallized_at": crystallized_at.isoformat(),
                "crystallized_child_count": 10,
            },
        )
        # 8 of 10 children updated after crystallization
        new_time = datetime.now(timezone.utc)
        old_time = crystallized_at - timedelta(hours=1)
        children = [_make_child(updated_at=new_time) for _ in range(8)]
        children += [_make_child(updated_at=old_time) for _ in range(2)]

        ctx = _make_ctx(node=node, children=children, generate_result="Re-crystallized.")

        pipeline = CrystallizationPipeline(ctx)
        result = await pipeline.check_and_crystallize(node.id)

        assert result is True
        ctx.model_gateway.generate.assert_awaited_once()

    @patch("kt_ontology.crystallization.get_settings")
    async def test_empty_llm_response(self, mock_settings: MagicMock) -> None:
        mock_settings.return_value.crystallization_child_threshold = 10
        mock_settings.return_value.crystallization_child_change_ratio = 0.5

        node = _make_node(concept="Test", metadata={})
        children = [_make_child() for _ in range(10)]
        ctx = _make_ctx(node=node, children=children, generate_result="")

        pipeline = CrystallizationPipeline(ctx)
        result = await pipeline.check_and_crystallize(node.id)

        assert result is False
        ctx.graph_engine.set_node_definition.assert_not_awaited()


# ══════════════════════════════════════════════════════════════════
# Prompt content tests
# ══════════════════════════════════════════════════════════════════


class TestCrystallizationPrompt:
    def test_system_prompt_has_principles(self) -> None:
        assert "Attribution-Grounded Tone" in CRYSTALLIZATION_SYSTEM_PROMPT
        assert "Radical Source Neutrality" in CRYSTALLIZATION_SYSTEM_PROMPT
        assert "Reason Through the Evidence" in CRYSTALLIZATION_SYSTEM_PROMPT
        assert "Preserve All Perspectives" in CRYSTALLIZATION_SYSTEM_PROMPT
        assert "Stakeholder Motivation Analysis" in CRYSTALLIZATION_SYSTEM_PROMPT
        assert "Ground Everything in Facts" in CRYSTALLIZATION_SYSTEM_PROMPT
        assert "Honest Assessment" in CRYSTALLIZATION_SYSTEM_PROMPT

    def test_system_prompt_has_response_structure(self) -> None:
        assert "Scope & Boundaries" in CRYSTALLIZATION_SYSTEM_PROMPT
        assert "Sub-domains" in CRYSTALLIZATION_SYSTEM_PROMPT
        assert "Tensions & Debates" in CRYSTALLIZATION_SYSTEM_PROMPT
        assert "Significance" in CRYSTALLIZATION_SYSTEM_PROMPT

    def test_system_prompt_no_tools(self) -> None:
        """Should not mention tools (get_node, get_node_facts, finish)."""
        assert "get_node(" not in CRYSTALLIZATION_SYSTEM_PROMPT
        assert "get_node_facts(" not in CRYSTALLIZATION_SYSTEM_PROMPT
        assert "finish(" not in CRYSTALLIZATION_SYSTEM_PROMPT
