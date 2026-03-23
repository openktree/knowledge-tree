"""Unit tests for extraction examples."""

from kt_facts.prompt.examples import (
    ALL_IMAGE_EXAMPLES,
    ALL_TEXT_EXAMPLES,
    TEST_BEFORE_EXTRACTING,
    ExtractionExample,
)


def test_render_good_example():
    ex = ExtractionExample(text="Test fact.", is_good=True, explanation="Has all parts.")
    rendered = ex.render()
    assert rendered.startswith('GOOD: "Test fact."')
    assert "Has all parts." in rendered


def test_render_bad_example():
    ex = ExtractionExample(text="Bad fact.", is_good=False, explanation="Missing predicate.")
    rendered = ex.render()
    assert rendered.startswith('BAD: "Bad fact."')
    assert "Missing predicate." in rendered


def test_all_text_examples_has_16_entries():
    """9 BAD + 7 GOOD = 16 total text examples."""
    assert len(ALL_TEXT_EXAMPLES) == 16


def test_all_text_examples_bad_count():
    bad = [e for e in ALL_TEXT_EXAMPLES if not e.is_good]
    assert len(bad) == 9


def test_all_text_examples_good_count():
    good = [e for e in ALL_TEXT_EXAMPLES if e.is_good]
    assert len(good) == 7


def test_all_image_examples_initially_empty():
    assert len(ALL_IMAGE_EXAMPLES) == 0


def test_test_before_extracting_instruction():
    assert "Test before extracting" in TEST_BEFORE_EXTRACTING
    assert "self-contained" in TEST_BEFORE_EXTRACTING


def test_all_examples_have_explanations():
    for ex in ALL_TEXT_EXAMPLES:
        assert len(ex.explanation) > 0, f"Example '{ex.text}' has empty explanation"


def test_example_is_frozen():
    """ExtractionExample is frozen (immutable)."""
    import pytest

    ex = ALL_TEXT_EXAMPLES[0]
    with pytest.raises(AttributeError):
        ex.text = "changed"  # type: ignore[misc]
