"""Unit tests for HttpxContentFetcher."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from kt_providers.fetch.httpx_provider import HttpxContentFetcher


def _mock_response(status: int = 200, content_type: str = "text/html", text: str = "", body: bytes = b"") -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status
    response.headers = {"content-type": content_type}
    response.text = text
    response.content = body
    if status >= 400:
        response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("err", request=MagicMock(), response=response)
        )
    else:
        response.raise_for_status = MagicMock()
    return response


def _patch_client(fetcher: HttpxContentFetcher, response: MagicMock) -> AsyncMock:
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=response)
    mock_client.is_closed = False
    fetcher._client = mock_client
    return mock_client


@pytest.mark.asyncio
async def test_provider_id_and_always_available():
    fetcher = HttpxContentFetcher()
    assert fetcher.provider_id == "httpx"
    assert await fetcher.is_available() is True


@pytest.mark.asyncio
async def test_html_success_via_trafilatura():
    fetcher = HttpxContentFetcher(timeout=5.0)
    extracted = "x" * 200
    _patch_client(fetcher, _mock_response(text="<html><body><p>...</p></body></html>"))

    with patch("kt_providers.fetch.extract.trafilatura") as mock_traf:
        mock_traf.extract.return_value = extracted
        mock_traf.metadata.extract_metadata.return_value = None
        result = await fetcher.fetch("https://example.com")

    assert result.success is True
    assert result.content == extracted
    await fetcher.close()


@pytest.mark.asyncio
async def test_timeout_returns_error_string():
    fetcher = HttpxContentFetcher(timeout=1.0)
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
    mock_client.is_closed = False
    fetcher._client = mock_client

    result = await fetcher.fetch("https://slow.example.com")

    assert result.success is False
    assert result.error == "Timeout"
    await fetcher.close()


@pytest.mark.asyncio
async def test_http_403_returns_status_in_error():
    fetcher = HttpxContentFetcher(timeout=5.0)
    _patch_client(fetcher, _mock_response(status=403))

    result = await fetcher.fetch("https://example.com/secret")

    assert result.success is False
    assert "403" in (result.error or "")
    await fetcher.close()


@pytest.mark.asyncio
async def test_image_returns_raw_bytes():
    fetcher = HttpxContentFetcher(timeout=5.0)
    _patch_client(fetcher, _mock_response(content_type="image/png", body=b"\x89PNG fake"))

    result = await fetcher.fetch("https://example.com/img.png")

    assert result.is_image is True
    assert result.raw_bytes == b"\x89PNG fake"
    await fetcher.close()


@pytest.mark.asyncio
async def test_non_text_content_type_skipped():
    fetcher = HttpxContentFetcher(timeout=5.0)
    _patch_client(fetcher, _mock_response(content_type="application/zip"))

    result = await fetcher.fetch("https://example.com/x.zip")

    assert result.success is False
    assert "Non-text" in (result.error or "")
    await fetcher.close()


@pytest.mark.asyncio
async def test_close_resets_client():
    fetcher = HttpxContentFetcher()
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False
    mock_client.aclose = AsyncMock()
    fetcher._client = mock_client

    await fetcher.close()

    mock_client.aclose.assert_called_once()
    assert fetcher._client is None
