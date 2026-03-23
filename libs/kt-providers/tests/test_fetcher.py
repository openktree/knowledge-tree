"""Unit tests for the ContentFetcher utility."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from kt_providers.fetcher import _MIN_EXTRACTED_LENGTH, ContentFetcher, FetchResult

# ── FetchResult tests ────────────────────────────────────────────


def test_fetch_result_success_with_content():
    r = FetchResult(uri="https://example.com", content="x" * _MIN_EXTRACTED_LENGTH)
    assert r.success is True


def test_fetch_result_failure_with_none():
    r = FetchResult(uri="https://example.com", content=None)
    assert r.success is False


def test_fetch_result_failure_with_short_content():
    r = FetchResult(uri="https://example.com", content="short")
    assert r.success is False


def test_fetch_result_failure_with_error():
    r = FetchResult(uri="https://example.com", error="Timeout")
    assert r.success is False


# ── ContentFetcher tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_url_html_success():
    """HTML content is extracted via trafilatura."""
    fetcher = ContentFetcher(timeout=5.0)

    html = "<html><body><p>This is a test article with enough content to pass the minimum length threshold for extraction.</p></body></html>"
    extracted_text = "This is a test article with enough content to pass the minimum length threshold for extraction."

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/html; charset=utf-8"}
    mock_response.text = html
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.is_closed = False
    fetcher._client = mock_client

    with patch("kt_providers.fetcher.trafilatura") as mock_traf:
        mock_traf.extract.return_value = extracted_text
        result = await fetcher.fetch_url("https://example.com/article")

    assert result.success is True
    assert result.content == extracted_text
    assert result.error is None
    await fetcher.close()


@pytest.mark.asyncio
async def test_fetch_url_timeout():
    """Timeout returns error result."""
    fetcher = ContentFetcher(timeout=1.0)

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
    mock_client.is_closed = False
    fetcher._client = mock_client

    result = await fetcher.fetch_url("https://slow.example.com")

    assert result.success is False
    assert result.error == "Timeout"
    await fetcher.close()


@pytest.mark.asyncio
async def test_fetch_url_http_error():
    """HTTP errors return error result."""
    fetcher = ContentFetcher(timeout=5.0)

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 403
    mock_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("Forbidden", request=MagicMock(), response=mock_response)
    )
    mock_response.headers = {"content-type": "text/html"}

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.is_closed = False
    fetcher._client = mock_client

    result = await fetcher.fetch_url("https://example.com/secret")

    assert result.success is False
    assert "403" in (result.error or "")
    await fetcher.close()


@pytest.mark.asyncio
async def test_fetch_url_pdf_content():
    """PDF content is extracted via pymupdf."""
    fetcher = ContentFetcher(timeout=5.0)

    # Create a real small PDF
    import pymupdf  # type: ignore[import-untyped]
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "This is extracted PDF content that should be long enough to pass the minimum.")
    pdf_bytes = doc.tobytes()
    doc.close()

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "application/pdf"}
    mock_response.content = pdf_bytes
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.is_closed = False
    fetcher._client = mock_client

    result = await fetcher.fetch_url("https://example.com/doc.pdf")

    assert result.success is True
    assert "extracted PDF content" in (result.content or "")
    assert result.content_type == "application/pdf"
    await fetcher.close()


@pytest.mark.asyncio
async def test_fetch_url_image_content():
    """Image content returns raw bytes for multimodal processing."""
    fetcher = ContentFetcher(timeout=5.0)

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "image/png"}
    mock_response.content = b"\x89PNG\r\n\x1a\nfake_image_data_here"
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.is_closed = False
    fetcher._client = mock_client

    result = await fetcher.fetch_url("https://example.com/chart.png")

    assert result.is_image is True
    assert result.raw_bytes == b"\x89PNG\r\n\x1a\nfake_image_data_here"
    assert result.content_type == "image/png"
    await fetcher.close()


@pytest.mark.asyncio
async def test_fetch_url_non_text_content():
    """Non-text/non-PDF/non-image content types are skipped."""
    fetcher = ContentFetcher(timeout=5.0)

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "application/zip"}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.is_closed = False
    fetcher._client = mock_client

    result = await fetcher.fetch_url("https://example.com/archive.zip")

    assert result.success is False
    assert "Non-text" in (result.error or "")
    await fetcher.close()


@pytest.mark.asyncio
async def test_fetch_url_plain_text():
    """Plain text content is returned as-is."""
    fetcher = ContentFetcher(timeout=5.0)
    plain_text = "x" * 100

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/plain"}
    mock_response.text = plain_text
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.is_closed = False
    fetcher._client = mock_client

    result = await fetcher.fetch_url("https://example.com/file.txt")

    assert result.success is True
    assert result.content == plain_text
    await fetcher.close()


@pytest.mark.asyncio
async def test_fetch_url_extraction_too_short():
    """When trafilatura extracts very little, it's treated as a failure."""
    fetcher = ContentFetcher(timeout=5.0)

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/html"}
    mock_response.text = "<html><body></body></html>"
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.is_closed = False
    fetcher._client = mock_client

    with patch("kt_providers.fetcher.trafilatura") as mock_traf:
        mock_traf.extract.return_value = "Hi"  # too short
        result = await fetcher.fetch_url("https://example.com/empty")

    assert result.success is False
    assert "insufficient" in (result.error or "").lower()
    await fetcher.close()


@pytest.mark.asyncio
async def test_fetch_urls_concurrent():
    """fetch_urls fetches multiple URLs concurrently."""
    fetcher = ContentFetcher(timeout=5.0, max_concurrent=2)

    text_content = "x" * 100

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/plain"}
    mock_response.text = text_content
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.is_closed = False
    fetcher._client = mock_client

    results = await fetcher.fetch_urls(["https://a.com", "https://b.com", "https://c.com"])

    assert len(results) == 3
    assert all(r.success for r in results)
    await fetcher.close()


@pytest.mark.asyncio
async def test_close_client():
    """close() properly closes the HTTP client."""
    fetcher = ContentFetcher()
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False
    mock_client.aclose = AsyncMock()
    fetcher._client = mock_client

    await fetcher.close()

    mock_client.aclose.assert_called_once()
    assert fetcher._client is None
