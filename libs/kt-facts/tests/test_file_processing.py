"""Unit tests for file-based fact extraction (PDF, image, FileDataStore)."""

import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from kt_facts.processing.file_processing import (
    build_image_extraction_messages,
    classify_content_type,
    extract_facts_from_image,
    extract_text_from_pdf,
)
from kt_providers.fetch import FetchResult, FileDataStore

# ── classify_content_type tests ─────────────────────────────────


def test_classify_pdf():
    assert classify_content_type("application/pdf") == "pdf"
    assert classify_content_type("Application/PDF; charset=utf-8") == "pdf"


def test_classify_image():
    assert classify_content_type("image/png") == "image"
    assert classify_content_type("image/jpeg") == "image"
    assert classify_content_type("image/webp") == "image"
    assert classify_content_type("image/gif") == "image"


def test_classify_text():
    assert classify_content_type("text/html") == "text"
    assert classify_content_type("text/plain") == "text"
    assert classify_content_type("application/json") == "text"
    assert classify_content_type("text/xml") == "text"


def test_classify_unknown():
    assert classify_content_type("application/octet-stream") == "unknown"
    assert classify_content_type("video/mp4") == "unknown"
    assert classify_content_type("application/zip") == "unknown"


# ── extract_text_from_pdf tests ─────────────────────────────────


def test_extract_text_from_pdf_basic():
    """Test PDF text extraction with a minimal valid PDF."""
    import pymupdf  # type: ignore[import-untyped]

    # Create a minimal PDF with pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello, this is a test PDF document.")
    pdf_bytes = doc.tobytes()
    doc.close()

    result = extract_text_from_pdf(pdf_bytes)
    assert "Hello" in result
    assert "test PDF document" in result


def test_extract_text_from_pdf_empty():
    """Empty PDF returns empty string."""
    import pymupdf  # type: ignore[import-untyped]

    doc = pymupdf.open()
    doc.new_page()  # blank page
    pdf_bytes = doc.tobytes()
    doc.close()

    result = extract_text_from_pdf(pdf_bytes)
    assert result == ""


def test_extract_text_from_pdf_multi_page():
    """Multi-page PDF returns text from all pages."""
    import pymupdf  # type: ignore[import-untyped]

    doc = pymupdf.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i + 1} content here.")
    pdf_bytes = doc.tobytes()
    doc.close()

    result = extract_text_from_pdf(pdf_bytes)
    assert "Page 1" in result
    assert "Page 2" in result
    assert "Page 3" in result


# ── build_image_extraction_messages tests ────────────────────────


def test_build_image_extraction_messages_structure():
    """Messages have correct multimodal structure."""
    image_bytes = b"\x89PNG\r\n\x1a\nfake_png_data"
    messages = build_image_extraction_messages(image_bytes, "image/png", "test concept")

    assert len(messages) == 1
    msg = messages[0]
    assert msg["role"] == "user"
    assert isinstance(msg["content"], list)
    assert len(msg["content"]) == 2

    # First part is the text prompt
    assert msg["content"][0]["type"] == "text"
    assert "test concept" in msg["content"][0]["text"]

    # Second part is the image
    assert msg["content"][1]["type"] == "image_url"
    url = msg["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")


def test_build_image_extraction_messages_base64_encoding():
    """Image bytes are correctly base64-encoded."""
    image_bytes = b"test_image_data_12345"
    messages = build_image_extraction_messages(image_bytes, "image/jpeg", "encoding test")

    url = messages[0]["content"][1]["image_url"]["url"]
    prefix = "data:image/jpeg;base64,"
    assert url.startswith(prefix)
    encoded_data = url[len(prefix) :]
    decoded = base64.b64decode(encoded_data)
    assert decoded == image_bytes


def test_build_image_extraction_messages_with_query_context():
    """Query context is included in the prompt when provided."""
    messages = build_image_extraction_messages(b"fake", "image/png", "test", query_context="how does X work?")

    text = messages[0]["content"][0]["text"]
    assert "Investigation context" in text
    assert "how does X work?" in text


def test_build_image_extraction_messages_without_query_context():
    """No investigation section when query_context is None."""
    messages = build_image_extraction_messages(b"fake", "image/png", "test", query_context=None)

    text = messages[0]["content"][0]["text"]
    assert "Investigation context" not in text


def test_build_image_extraction_messages_fallback_mime_type():
    """Non-image content type falls back to image/png."""
    messages = build_image_extraction_messages(b"fake", "application/octet-stream", "test")

    url = messages[0]["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")


# ── extract_facts_from_image tests ───────────────────────────────


@pytest.mark.asyncio
async def test_extract_facts_from_image_success():
    """Successful image extraction returns facts and description."""
    gateway = MagicMock()
    gateway.file_decomposition_model = "test-vision-model"
    gateway.generate_json = AsyncMock(
        return_value={
            "facts": [
                {
                    "content": "The chart shows GDP growth of 3.2%",
                    "fact_type": "measurement",
                    "who": None,
                    "where": None,
                    "when": "2025",
                    "context": "Economic chart",
                },
                {
                    "content": "Bar graph with blue and red colors",
                    "fact_type": "image",
                    "who": None,
                    "where": None,
                    "when": None,
                    "context": None,
                },
            ]
        }
    )

    facts, description = await extract_facts_from_image(
        b"fake_image_bytes",
        "image/png",
        "GDP growth",
        gateway,
    )

    assert len(facts) == 2
    assert facts[0].content == "The chart shows GDP growth of 3.2%"
    assert facts[0].fact_type == "measurement"
    assert facts[1].fact_type == "image"
    assert "[Image: GDP growth]" in description
    assert "GDP growth of 3.2%" in description

    # Verify the gateway was called with multimodal messages
    call_args = gateway.generate_json.call_args
    messages = call_args.kwargs["messages"]
    assert isinstance(messages[0]["content"], list)


@pytest.mark.asyncio
async def test_extract_facts_from_image_no_facts():
    """When no facts are extracted, returns empty list and appropriate description."""
    gateway = MagicMock()
    gateway.file_decomposition_model = "test-vision-model"
    gateway.generate_json = AsyncMock(return_value={"facts": []})

    facts, description = await extract_facts_from_image(
        b"blank_image",
        "image/jpeg",
        "empty concept",
        gateway,
    )

    assert facts == []
    assert "No extractable content" in description


@pytest.mark.asyncio
async def test_extract_facts_from_image_error():
    """On error, returns empty list and error description."""
    gateway = MagicMock()
    gateway.file_decomposition_model = "test-vision-model"
    gateway.generate_json = AsyncMock(side_effect=RuntimeError("API down"))

    facts, description = await extract_facts_from_image(
        b"fake",
        "image/png",
        "error test",
        gateway,
    )

    assert facts == []
    assert "Extraction failed" in description


# ── FileDataStore tests ──────────────────────────────────────────


def test_file_data_store_store_and_get():
    store = FileDataStore()
    store.store("https://example.com/image.png", b"image_data")
    assert store.get("https://example.com/image.png") == b"image_data"


def test_file_data_store_get_missing():
    store = FileDataStore()
    assert store.get("https://nonexistent.com/img.png") is None


def test_file_data_store_remove():
    store = FileDataStore()
    store.store("https://example.com/a.png", b"data_a")
    store.remove("https://example.com/a.png")
    assert store.get("https://example.com/a.png") is None


def test_file_data_store_remove_missing():
    """Removing a nonexistent key does not raise."""
    store = FileDataStore()
    store.remove("https://example.com/missing.png")  # should not raise


def test_file_data_store_clear():
    store = FileDataStore()
    store.store("a", b"1")
    store.store("b", b"2")
    store.clear()
    assert store.get("a") is None
    assert store.get("b") is None


def test_file_data_store_has():
    store = FileDataStore()
    assert store.has("key") is False
    store.store("key", b"val")
    assert store.has("key") is True


# ── FetchResult.is_image tests ───────────────────────────────────


def test_fetch_result_is_image_true():
    r = FetchResult(uri="https://example.com/img.png", content="placeholder", content_type="image/png")
    assert r.is_image is True


def test_fetch_result_is_image_jpeg():
    r = FetchResult(uri="https://example.com/img.jpg", content="placeholder", content_type="image/jpeg")
    assert r.is_image is True


def test_fetch_result_is_image_false_text():
    r = FetchResult(uri="https://example.com/page", content="text", content_type="text/html")
    assert r.is_image is False


def test_fetch_result_is_image_false_none():
    r = FetchResult(uri="https://example.com/page", content="text")
    assert r.is_image is False


def test_fetch_result_is_image_false_pdf():
    r = FetchResult(uri="https://example.com/doc.pdf", content="text", content_type="application/pdf")
    assert r.is_image is False
