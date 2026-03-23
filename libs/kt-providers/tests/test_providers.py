import httpx
import pytest

from kt_config.types import RawSearchResult
from kt_providers.base import KnowledgeProvider
from kt_providers.registry import ProviderRegistry


class MockProvider(KnowledgeProvider):
    def __init__(self, pid: str, results: list[RawSearchResult] | None = None):
        self._pid = pid
        self._results = results or []
        self._available = True

    @property
    def provider_id(self) -> str:
        return self._pid

    async def search(self, query: str, max_results: int = 10) -> list[RawSearchResult]:
        return self._results[:max_results]

    async def is_available(self) -> bool:
        return self._available


def _make_result(uri: str, title: str = "Test") -> RawSearchResult:
    return RawSearchResult(
        uri=uri,
        title=title,
        raw_content=f"Content for {uri}",
        provider_id="test",
    )


def test_registry_register_and_get() -> None:
    registry = ProviderRegistry()
    provider = MockProvider("test_provider")
    registry.register(provider)
    assert registry.get("test_provider") is provider
    assert registry.get("nonexistent") is None


def test_registry_get_all() -> None:
    registry = ProviderRegistry()
    p1 = MockProvider("p1")
    p2 = MockProvider("p2")
    registry.register(p1)
    registry.register(p2)
    assert len(registry.get_all()) == 2


@pytest.mark.asyncio
async def test_registry_search_all_dedup() -> None:
    """search_all should deduplicate results by URI across providers."""
    shared_result = _make_result("https://example.com/shared")
    unique_result = _make_result("https://example.com/unique")

    p1 = MockProvider("p1", [shared_result, unique_result])
    p2 = MockProvider("p2", [shared_result])  # duplicate URI

    registry = ProviderRegistry()
    registry.register(p1)
    registry.register(p2)

    results = await registry.search_all("test query")
    uris = [r.uri for r in results]
    assert len(uris) == 2
    assert "https://example.com/shared" in uris
    assert "https://example.com/unique" in uris


@pytest.mark.asyncio
async def test_registry_search_all_skips_unavailable() -> None:
    """search_all should skip unavailable providers."""
    p1 = MockProvider("p1", [_make_result("https://a.com")])
    p2 = MockProvider("p2", [_make_result("https://b.com")])
    p2._available = False

    registry = ProviderRegistry()
    registry.register(p1)
    registry.register(p2)

    results = await registry.search_all("test")
    assert len(results) == 1
    assert results[0].uri == "https://a.com"


@pytest.mark.asyncio
async def test_brave_retries_on_429(respx_mock) -> None:
    """BraveSearchProvider should retry on 429 with exponential backoff."""
    from unittest.mock import AsyncMock, patch

    from kt_providers.brave import BraveSearchProvider

    url = BraveSearchProvider.SEARCH_URL
    respx_mock.get(url).mock(
        side_effect=[
            httpx.Response(429, text="rate limited"),
            httpx.Response(
                200, json={"web": {"results": [{"url": "https://x.com", "title": "X", "description": "d"}]}}
            ),
        ]
    )

    provider = BraveSearchProvider(api_key="test-key")

    with patch("kt_providers.brave.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        results = await provider.search("test", max_results=1)

    assert len(results) == 1
    assert results[0].uri == "https://x.com"
    mock_sleep.assert_called_once_with(1.0)  # BASE_DELAY * 2^0


@pytest.mark.asyncio
async def test_brave_raises_after_max_retries(respx_mock) -> None:
    """BraveSearchProvider should raise after exhausting retries."""
    from unittest.mock import AsyncMock, patch

    from kt_providers.brave import BraveSearchProvider

    url = BraveSearchProvider.SEARCH_URL
    respx_mock.get(url).mock(return_value=httpx.Response(429, text="rate limited"))

    provider = BraveSearchProvider(api_key="test-key")

    with (
        patch("kt_providers.brave.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        pytest.raises(httpx.HTTPStatusError),
    ):
        await provider.search("test", max_results=1)

    assert mock_sleep.call_count == 3  # MAX_RETRIES attempts


@pytest.mark.asyncio
async def test_brave_no_retry_on_4xx(respx_mock) -> None:
    """BraveSearchProvider should NOT retry on non-retryable errors like 401."""
    from kt_providers.brave import BraveSearchProvider

    url = BraveSearchProvider.SEARCH_URL
    respx_mock.get(url).mock(return_value=httpx.Response(401, text="unauthorized"))

    provider = BraveSearchProvider(api_key="bad-key")

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await provider.search("test", max_results=1)

    assert exc_info.value.response.status_code == 401


@pytest.mark.asyncio
async def test_serper_search_success(respx_mock) -> None:
    """SerperSearchProvider should map response fields to RawSearchResult."""
    from kt_providers.serper import SerperSearchProvider

    url = SerperSearchProvider.SEARCH_URL
    respx_mock.post(url).mock(
        return_value=httpx.Response(
            200,
            json={
                "organic": [
                    {
                        "title": "Example Title",
                        "link": "https://example.com",
                        "snippet": "A short description.",
                        "position": 1,
                        "date": "2025-01-15",
                    }
                ]
            },
        )
    )

    provider = SerperSearchProvider(api_key="test-key")
    results = await provider.search("test query", max_results=5)

    assert len(results) == 1
    assert results[0].uri == "https://example.com"
    assert results[0].title == "Example Title"
    # Title should NOT be in raw_content (stored separately to prevent title-as-fact extraction)
    assert "Example Title" not in results[0].raw_content
    assert "A short description." in results[0].raw_content
    assert results[0].provider_id == "serper"
    assert results[0].provider_metadata == {"position": 1, "date": "2025-01-15"}


@pytest.mark.asyncio
async def test_serper_retries_on_429(respx_mock) -> None:
    """SerperSearchProvider should retry on 429 with exponential backoff."""
    from unittest.mock import AsyncMock, patch

    from kt_providers.serper import SerperSearchProvider

    url = SerperSearchProvider.SEARCH_URL
    respx_mock.post(url).mock(
        side_effect=[
            httpx.Response(429, text="rate limited"),
            httpx.Response(
                200,
                json={"organic": [{"title": "X", "link": "https://x.com", "snippet": "d"}]},
            ),
        ]
    )

    provider = SerperSearchProvider(api_key="test-key")

    with patch("kt_providers.serper.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        results = await provider.search("test", max_results=1)

    assert len(results) == 1
    assert results[0].uri == "https://x.com"
    mock_sleep.assert_called_once_with(1.0)  # BASE_DELAY * 2^0


@pytest.mark.asyncio
async def test_serper_raises_after_max_retries(respx_mock) -> None:
    """SerperSearchProvider should raise after exhausting retries."""
    from unittest.mock import AsyncMock, patch

    from kt_providers.serper import SerperSearchProvider

    url = SerperSearchProvider.SEARCH_URL
    respx_mock.post(url).mock(return_value=httpx.Response(429, text="rate limited"))

    provider = SerperSearchProvider(api_key="test-key")

    with (
        patch("kt_providers.serper.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        pytest.raises(httpx.HTTPStatusError),
    ):
        await provider.search("test", max_results=1)

    assert mock_sleep.call_count == 3  # MAX_RETRIES attempts


@pytest.mark.asyncio
async def test_serper_no_retry_on_4xx(respx_mock) -> None:
    """SerperSearchProvider should NOT retry on non-retryable errors like 401."""
    from kt_providers.serper import SerperSearchProvider

    url = SerperSearchProvider.SEARCH_URL
    respx_mock.post(url).mock(return_value=httpx.Response(401, text="unauthorized"))

    provider = SerperSearchProvider(api_key="bad-key")

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await provider.search("test", max_results=1)

    assert exc_info.value.response.status_code == 401


def test_source_hash_consistency() -> None:
    from kt_db.repositories.sources import SourceRepository

    h1 = SourceRepository.compute_hash("hello world")
    h2 = SourceRepository.compute_hash("hello world")
    h3 = SourceRepository.compute_hash("different content")
    assert h1 == h2
    assert h1 != h3
