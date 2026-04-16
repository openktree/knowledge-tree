import pytest

from kt_config.types import RawSearchResult
from kt_core_engine_api.search import KnowledgeProvider
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


def test_source_hash_consistency() -> None:
    from kt_db.repositories.sources import SourceRepository

    h1 = SourceRepository.compute_hash("hello world")
    h2 = SourceRepository.compute_hash("hello world")
    h3 = SourceRepository.compute_hash("different content")
    assert h1 == h2
    assert h1 != h3
