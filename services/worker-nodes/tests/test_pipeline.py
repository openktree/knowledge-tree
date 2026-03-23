"""Unit tests for the fact decomposition pipeline's parsing logic."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from kt_facts.extraction import TextExtractor
from kt_facts.models import (
    ExtractedFactWithAttribution,
    _format_attribution,
    parse_extraction_result,
)
from kt_facts.prompt import TEXT_PROMPT_BUILDER


def test_parse_extraction_result_valid():
    data = {
        "facts": [
            {
                "content": "Water boils at 100C.",
                "fact_type": "measurement",
                "who": "NASA",
                "where": "Space.com",
                "when": "2024",
                "context": "Science article",
            }
        ]
    }
    result = parse_extraction_result(data)
    assert len(result) == 1
    assert result[0].content == "Water boils at 100C."
    assert result[0].fact_type == "measurement"
    assert result[0].who == "NASA"
    assert result[0].where == "Space.com"


def test_parse_extraction_result_cleans_nulls():
    data = {
        "facts": [
            {
                "content": "Test fact.",
                "fact_type": "claim",
                "who": "null",
                "where": "None",
                "when": "n/a",
                "context": "",
            }
        ]
    }
    result = parse_extraction_result(data)
    assert len(result) == 1
    assert result[0].who is None
    assert result[0].where is None
    assert result[0].when is None
    assert result[0].context is None


def test_parse_extraction_result_missing_content():
    data = {"facts": [{"fact_type": "claim"}]}
    result = parse_extraction_result(data)
    assert len(result) == 0


def test_parse_extraction_result_empty_facts():
    data = {"facts": []}
    result = parse_extraction_result(data)
    assert len(result) == 0


def test_parse_extraction_result_no_facts_key():
    data = {}
    result = parse_extraction_result(data)
    assert len(result) == 0


def test_parse_extraction_result_defaults_type():
    data = {
        "facts": [
            {
                "content": "Some fact.",
            }
        ]
    }
    result = parse_extraction_result(data)
    assert len(result) == 1
    assert result[0].fact_type == "claim"


def test_format_attribution_full():
    ef = ExtractedFactWithAttribution(
        content="test",
        fact_type="claim",
        who="NASA",
        where="Space.com",
        when="2024",
        context="Space article",
    )
    result = _format_attribution(ef)
    assert result == "who: NASA; where: Space.com; when: 2024; context: Space article"


def test_format_attribution_partial():
    ef = ExtractedFactWithAttribution(
        content="test",
        fact_type="claim",
        who="Einstein",
    )
    result = _format_attribution(ef)
    assert result == "who: Einstein"


def test_format_attribution_none():
    ef = ExtractedFactWithAttribution(
        content="test",
        fact_type="claim",
    )
    result = _format_attribution(ef)
    assert result is None


# ── query_context tests ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_extract_chunk_with_query_context_includes_investigation_section():
    """When query_context is provided, the prompt should include the investigation section."""
    gateway = MagicMock()
    gateway.decomposition_model = "test-model"
    gateway.decomposition_thinking_level = None
    gateway.generate_json = AsyncMock(return_value={"facts": []})

    extractor = TextExtractor(gateway)
    await extractor._extract_chunk("Some text about the moon.", "moon", query_context="is the moon artificial?")

    call_args = gateway.generate_json.call_args
    prompt = call_args.kwargs.get("messages") or call_args[1]["messages"]
    prompt_text = prompt[0]["content"]

    assert "Investigation context" in prompt_text
    assert "is the moon artificial?" in prompt_text


@pytest.mark.asyncio
async def test_extract_chunk_without_query_context_excludes_investigation_section():
    """When query_context is None, the prompt should NOT include the investigation section."""
    gateway = MagicMock()
    gateway.decomposition_model = "test-model"
    gateway.decomposition_thinking_level = None
    gateway.generate_json = AsyncMock(return_value={"facts": []})

    extractor = TextExtractor(gateway)
    await extractor._extract_chunk("Some text about the moon.", "moon", query_context=None)

    call_args = gateway.generate_json.call_args
    prompt = call_args.kwargs.get("messages") or call_args[1]["messages"]
    prompt_text = prompt[0]["content"]

    assert "Investigation context" not in prompt_text


@pytest.mark.asyncio
async def test_extract_chunk_empty_query_context_excludes_investigation_section():
    """When query_context is empty string, the prompt should NOT include the investigation section."""
    gateway = MagicMock()
    gateway.decomposition_model = "test-model"
    gateway.decomposition_thinking_level = None
    gateway.generate_json = AsyncMock(return_value={"facts": []})

    extractor = TextExtractor(gateway)
    await extractor._extract_chunk("Some text about the moon.", "moon", query_context="")

    call_args = gateway.generate_json.call_args
    prompt = call_args.kwargs.get("messages") or call_args[1]["messages"]
    prompt_text = prompt[0]["content"]

    assert "Investigation context" not in prompt_text


@pytest.mark.asyncio
async def test_extract_chunk_query_context_preserves_source_text():
    """The source text should still appear in the prompt when query_context is set."""
    gateway = MagicMock()
    gateway.decomposition_model = "test-model"
    gateway.decomposition_thinking_level = None
    gateway.generate_json = AsyncMock(return_value={"facts": []})

    extractor = TextExtractor(gateway)
    await extractor._extract_chunk("Unique source content here.", "test", query_context="test query")

    call_args = gateway.generate_json.call_args
    prompt = call_args.kwargs.get("messages") or call_args[1]["messages"]
    prompt_text = prompt[0]["content"]

    assert "Unique source content here." in prompt_text
    assert "Source text:" in prompt_text


def test_text_prompt_includes_fragment_guidance():
    """The text prompt builder should include guidance on rejecting incomplete fragments."""
    prompt = TEXT_PROMPT_BUILDER.build_text_prompt("test", "test text")
    assert "Reject incomplete fragments" in prompt


def test_text_prompt_includes_query_context_section():
    """Verify the prompt builder includes query context when provided."""
    prompt = TEXT_PROMPT_BUILDER.build_text_prompt("test", "test text", query_context="how does gravity work?")
    assert "how does gravity work?" in prompt
    assert "Investigation context" in prompt
    assert "extract ALL facts" in prompt
