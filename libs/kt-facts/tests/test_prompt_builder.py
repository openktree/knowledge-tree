"""Unit tests for the extraction prompt builder."""

from kt_facts.prompt.builder import (
    IMAGE_PROMPT_BUILDER,
    TEXT_PROMPT_BUILDER,
    ExtractionPromptBuilder,
)
from kt_facts.prompt.types import ALL_FACT_TYPES, IMAGE_FACT_TYPES


def test_text_prompt_contains_all_type_names():
    """All 12 fact type names appear in the text extraction prompt."""
    prompt = TEXT_PROMPT_BUILDER.build_text_prompt("test concept", "test source text")
    for spec in ALL_FACT_TYPES:
        assert f"**{spec.name}**" in prompt, f"Missing type '{spec.name}' in text prompt"


def test_image_prompt_contains_image_type_names():
    """Only the 8 image-applicable type names appear in the image prompt."""
    prompt = IMAGE_PROMPT_BUILDER.build_image_prompt("test concept")
    for spec in IMAGE_FACT_TYPES:
        assert f"**{spec.name}**" in prompt, f"Missing type '{spec.name}' in image prompt"


def test_image_prompt_excludes_text_only_types():
    """Text-only types (account, formula, code) do NOT appear in image prompt."""
    prompt = IMAGE_PROMPT_BUILDER.build_image_prompt("test concept")
    excluded = {"account", "formula", "code"}
    for name in excluded:
        assert f"**{name}**" not in prompt, f"Type '{name}' should not be in image prompt"


def test_text_prompt_query_context_injection():
    """Query context section appears when provided."""
    prompt = TEXT_PROMPT_BUILDER.build_text_prompt(
        "test",
        "some text",
        query_context="how does gravity work?",
    )
    assert "Investigation context" in prompt
    assert "how does gravity work?" in prompt
    assert "extract ALL facts" in prompt


def test_text_prompt_no_query_context():
    """Investigation context section is absent when query_context is None."""
    prompt = TEXT_PROMPT_BUILDER.build_text_prompt("test", "some text", query_context=None)
    assert "Investigation context" not in prompt


def test_text_prompt_source_text_at_end():
    """Source text appears at the end of the text prompt."""
    prompt = TEXT_PROMPT_BUILDER.build_text_prompt("test", "UNIQUE_SOURCE_MARKER")
    assert "UNIQUE_SOURCE_MARKER" in prompt
    # Source text should be after the response format
    response_idx = prompt.find("Response format")
    source_idx = prompt.find("UNIQUE_SOURCE_MARKER")
    assert source_idx > response_idx


def test_text_prompt_response_format_present():
    """Response format section is present."""
    prompt = TEXT_PROMPT_BUILDER.build_text_prompt("test", "text")
    assert "Response format" in prompt
    assert '"content"' in prompt
    assert '"fact_type"' in prompt


def test_image_prompt_response_format_present():
    """Response format section is present in image prompt."""
    prompt = IMAGE_PROMPT_BUILDER.build_image_prompt("test")
    assert "Response format" in prompt
    assert '"content"' in prompt


def test_text_prompt_examples_section_present():
    """Examples section with BAD and GOOD labels is present."""
    prompt = TEXT_PROMPT_BUILDER.build_text_prompt("test", "text")
    assert "BAD:" in prompt
    assert "GOOD:" in prompt
    assert "Test before extracting" in prompt


def test_text_prompt_concept_in_intro():
    """Concept name appears in the system intro."""
    prompt = TEXT_PROMPT_BUILDER.build_text_prompt("quantum computing", "text about QC")
    assert "quantum computing" in prompt


def test_image_prompt_concept_in_intro():
    """Concept name appears in the image system intro."""
    prompt = IMAGE_PROMPT_BUILDER.build_image_prompt("neural networks")
    assert "neural networks" in prompt


def test_image_prompt_has_visual_intro():
    """Image prompt uses visual-specific intro language."""
    prompt = IMAGE_PROMPT_BUILDER.build_image_prompt("test")
    assert "visual fact extraction" in prompt


def test_text_prompt_has_text_intro():
    """Text prompt uses text-specific intro language."""
    prompt = TEXT_PROMPT_BUILDER.build_text_prompt("test", "text")
    assert "fact extraction and attribution" in prompt


def test_text_prompt_response_format_no_mentions():
    """Response format does NOT include mentions (entity extraction is separate)."""
    prompt = TEXT_PROMPT_BUILDER.build_text_prompt("test concept", "test text")
    assert '"mentions"' not in prompt


def test_custom_builder():
    """Custom builder with subset of types/constraints/examples works."""
    from kt_facts.prompt.constraints import EXTRACTION_RULES
    from kt_facts.prompt.examples import GOOD_INVENTION
    from kt_facts.prompt.types import CLAIM, MEASUREMENT

    builder = ExtractionPromptBuilder(
        fact_types=[CLAIM, MEASUREMENT],
        constraints=[EXTRACTION_RULES],
        examples=[GOOD_INVENTION],
    )
    prompt = builder.build_text_prompt("test", "test text")

    assert "**claim**" in prompt
    assert "**measurement**" in prompt
    assert "**reference**" not in prompt  # not included
    assert "GOOD:" in prompt
    assert "Kumar Patel" in prompt
