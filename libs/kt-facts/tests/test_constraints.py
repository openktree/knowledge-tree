"""Unit tests for extraction constraints."""

from kt_facts.prompt.constraints import (
    ALL_IMAGE_CONSTRAINTS,
    ALL_TEXT_CONSTRAINTS,
    EXTRACTION_RULES,
    FACT_STRUCTURE,
    IMAGE_RULES,
    IMAGE_SELF_CONTAINMENT,
    SELF_CONTAINMENT,
    SKIP_RULES,
    ExtractionConstraint,
)


def test_render_includes_heading_and_body():
    constraint = ExtractionConstraint(
        heading="Test Heading",
        body="Test body content.",
        priority=0,
    )
    rendered = constraint.render()
    assert "## Test Heading" in rendered
    assert "Test body content." in rendered


def test_priority_ordering():
    """Text constraints are ordered by priority (ascending)."""
    priorities = [c.priority for c in ALL_TEXT_CONSTRAINTS]
    assert priorities == sorted(priorities)


def test_all_text_constraints_has_4_entries():
    assert len(ALL_TEXT_CONSTRAINTS) == 4


def test_all_image_constraints_has_2_entries():
    assert len(ALL_IMAGE_CONSTRAINTS) == 2


def test_extraction_rules_priority():
    assert EXTRACTION_RULES.priority == 5


def test_fact_structure_priority():
    assert FACT_STRUCTURE.priority == 10


def test_self_containment_priority():
    assert SELF_CONTAINMENT.priority == 20


def test_skip_rules_priority():
    assert SKIP_RULES.priority == 30


def test_image_rules_priority():
    assert IMAGE_RULES.priority == 5


def test_image_self_containment_priority():
    assert IMAGE_SELF_CONTAINMENT.priority == 20


def test_text_constraints_contain_key_phrases():
    """Spot-check that text constraints contain expected content."""
    rules_text = EXTRACTION_RULES.render()
    assert "Extract ONLY information explicitly stated" in rules_text

    structure_text = FACT_STRUCTURE.render()
    assert "complete assertion" in structure_text

    self_contained_text = SELF_CONTAINMENT.render()
    assert "self-contained" in self_contained_text

    skip_text = SKIP_RULES.render()
    assert "Platform metrics" in skip_text


def test_image_constraints_contain_key_phrases():
    """Spot-check that image constraints contain expected content."""
    rules_text = IMAGE_RULES.render()
    assert "Extract ONLY information visible in the image" in rules_text

    self_contained_text = IMAGE_SELF_CONTAINMENT.render()
    assert "self-contained" in self_contained_text


def test_constraint_is_frozen():
    """ExtractionConstraint is frozen (immutable)."""
    import pytest

    with pytest.raises(AttributeError):
        EXTRACTION_RULES.heading = "changed"  # type: ignore[misc]
