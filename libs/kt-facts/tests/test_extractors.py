"""Unit tests for fact extractors (TextExtractor, ImageExtractor)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from kt_facts.extraction import ImageExtractor, TextExtractor


@pytest.fixture
def mock_gateway() -> MagicMock:
    gw = MagicMock()
    gw.decomposition_model = "test-model"
    gw.decomposition_thinking_level = None
    gw.file_decomposition_model = "test-vision-model"
    gw.file_decomposition_thinking_level = None
    gw.generate_json = AsyncMock(return_value={"facts": []})
    return gw


# ── TextExtractor tests ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_text_extractor_calls_gateway(mock_gateway: MagicMock):
    """TextExtractor sends the prompt to the gateway."""
    extractor = TextExtractor(mock_gateway)
    result = await extractor.extract("Hello world.", "test concept")

    assert result == []
    mock_gateway.generate_json.assert_called_once()
    call_kwargs = mock_gateway.generate_json.call_args.kwargs
    assert call_kwargs["model_id"] == "test-model"
    prompt = call_kwargs["messages"][0]["content"]
    assert "test concept" in prompt
    assert "Hello world." in prompt


@pytest.mark.asyncio
async def test_text_extractor_returns_parsed_facts(mock_gateway: MagicMock):
    """TextExtractor returns parsed facts from the LLM response."""
    mock_gateway.generate_json = AsyncMock(
        return_value={
            "facts": [
                {"content": "Fact one.", "fact_type": "claim"},
                {"content": "Fact two.", "fact_type": "measurement"},
            ]
        }
    )

    extractor = TextExtractor(mock_gateway)
    result = await extractor.extract("Some content.", "test")

    assert len(result) == 2
    assert result[0].content == "Fact one."
    assert result[1].fact_type == "measurement"


@pytest.mark.asyncio
async def test_text_extractor_with_query_context(mock_gateway: MagicMock):
    """TextExtractor includes query context in the prompt."""
    extractor = TextExtractor(mock_gateway)
    await extractor.extract("Some text.", "test", query_context="how does X work?")

    prompt = mock_gateway.generate_json.call_args.kwargs["messages"][0]["content"]
    assert "Investigation context" in prompt
    assert "how does X work?" in prompt


@pytest.mark.asyncio
async def test_text_extractor_handles_llm_error(mock_gateway: MagicMock):
    """TextExtractor returns [] on LLM error."""
    mock_gateway.generate_json = AsyncMock(side_effect=RuntimeError("API error"))

    extractor = TextExtractor(mock_gateway)
    result = await extractor.extract("text", "concept")

    assert result == []


@pytest.mark.asyncio
async def test_text_extractor_empty_content(mock_gateway: MagicMock):
    """TextExtractor returns [] for empty content."""
    extractor = TextExtractor(mock_gateway)
    result = await extractor.extract("", "concept")

    assert result == []
    mock_gateway.generate_json.assert_not_called()


@pytest.mark.asyncio
async def test_text_extractor_id():
    gw = MagicMock()
    extractor = TextExtractor(gw)
    assert extractor.extractor_id == "text"


# ── ImageExtractor tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_image_extractor_builds_multimodal_messages(mock_gateway: MagicMock):
    """ImageExtractor sends multimodal messages to the gateway."""
    extractor = ImageExtractor(mock_gateway)
    await extractor.extract(b"fake_image", "test concept", content_type="image/png")

    mock_gateway.generate_json.assert_called_once()
    call_kwargs = mock_gateway.generate_json.call_args.kwargs
    assert call_kwargs["model_id"] == "test-vision-model"
    messages = call_kwargs["messages"]
    assert len(messages) == 1
    content = messages[0]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert "test concept" in content[0]["text"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_image_extractor_returns_parsed_facts(mock_gateway: MagicMock):
    """ImageExtractor returns parsed facts."""
    mock_gateway.generate_json = AsyncMock(
        return_value={
            "facts": [
                {"content": "Chart shows growth.", "fact_type": "measurement"},
            ]
        }
    )

    extractor = ImageExtractor(mock_gateway)
    result = await extractor.extract(b"fake", "test", content_type="image/png")

    assert len(result) == 1
    assert result[0].content == "Chart shows growth."


@pytest.mark.asyncio
async def test_image_extractor_with_description(mock_gateway: MagicMock):
    """extract_with_description returns (facts, description)."""
    mock_gateway.generate_json = AsyncMock(
        return_value={
            "facts": [
                {"content": "Revenue grew 10%.", "fact_type": "measurement"},
            ]
        }
    )

    extractor = ImageExtractor(mock_gateway)
    facts, description = await extractor.extract_with_description(
        b"fake",
        "image/png",
        "revenue",
    )

    assert len(facts) == 1
    assert "[Image: revenue]" in description
    assert "Revenue grew 10%" in description


@pytest.mark.asyncio
async def test_image_extractor_empty_description(mock_gateway: MagicMock):
    """When no facts extracted, description says so."""
    mock_gateway.generate_json = AsyncMock(return_value={"facts": []})

    extractor = ImageExtractor(mock_gateway)
    facts, description = await extractor.extract_with_description(
        b"blank",
        "image/png",
        "test",
    )

    assert facts == []
    assert "No extractable content" in description


@pytest.mark.asyncio
async def test_image_extractor_handles_error(mock_gateway: MagicMock):
    """ImageExtractor returns [] on LLM error."""
    mock_gateway.generate_json = AsyncMock(side_effect=RuntimeError("API down"))

    extractor = ImageExtractor(mock_gateway)
    result = await extractor.extract(b"img", "concept", content_type="image/png")

    assert result == []


@pytest.mark.asyncio
async def test_image_extractor_id():
    gw = MagicMock()
    extractor = ImageExtractor(gw)
    assert extractor.extractor_id == "image"
