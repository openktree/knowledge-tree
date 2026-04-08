"""Integration test: real HTTP fetch via the httpx fetch provider.

These tests make actual network requests. They are skipped when the network
is unavailable.
"""

from __future__ import annotations

import httpx
import pytest

from kt_providers.fetch.httpx_provider import HttpxContentFetcher


def _network_available() -> bool:
    """Quick check if we can reach the internet."""
    try:
        httpx.get("https://httpbin.org/status/200", timeout=5)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _network_available(),
    reason="Network unavailable — skipping real HTTP tests",
)


@pytest.mark.asyncio
async def test_fetch_real_html_page():
    """Fetch a real HTML page and verify extracted text length."""
    fetcher = HttpxContentFetcher(timeout=15.0)
    try:
        result = await fetcher.fetch("https://httpbin.org/html")
        assert result.success is True
        assert result.content is not None
        assert len(result.content) >= 50
    finally:
        await fetcher.close()


@pytest.mark.asyncio
async def test_fetch_real_json_content():
    """Fetch a JSON URL (text content type, returned as-is)."""
    fetcher = HttpxContentFetcher(timeout=15.0)
    try:
        result = await fetcher.fetch("https://httpbin.org/get")
        assert result.success is True
        assert result.content is not None
        assert "headers" in result.content.lower() or "origin" in result.content.lower()
    finally:
        await fetcher.close()


@pytest.mark.asyncio
async def test_fetch_real_404():
    """A 404 page should fail gracefully."""
    fetcher = HttpxContentFetcher(timeout=15.0)
    try:
        result = await fetcher.fetch("https://httpbin.org/status/404")
        assert result.success is False
        assert result.error is not None
        assert "404" in result.error
    finally:
        await fetcher.close()
