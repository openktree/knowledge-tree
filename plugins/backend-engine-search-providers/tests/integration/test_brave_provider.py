import pytest

from kt_core_engine_api.search import RawSearchResult
from kt_db.repositories.sources import SourceRepository
from kt_plugin_be_search_providers.providers.brave import BraveSearchProvider
from kt_plugin_be_search_providers.settings import SearchProvidersSettings

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_brave_search_returns_results() -> None:
    """Integration test: call real Brave Search API."""
    settings = SearchProvidersSettings()
    if not settings.brave_key:
        pytest.skip("BRAVE_KEY not set")

    provider = BraveSearchProvider(api_key=settings.brave_key)
    try:
        results = await provider.search("what is photosynthesis", max_results=5)
        assert len(results) > 0
        for r in results:
            assert r.uri
            assert r.title
            assert r.raw_content
            assert r.provider_id == "brave_search"
    finally:
        await provider.close()


async def test_brave_provider_is_available() -> None:
    settings = SearchProvidersSettings()
    if not settings.brave_key:
        pytest.skip("BRAVE_KEY not set")

    provider = BraveSearchProvider(api_key=settings.brave_key)
    assert await provider.is_available() is True

    empty_provider = BraveSearchProvider(api_key="")
    assert await empty_provider.is_available() is False


async def test_source_repository_create_and_dedup(db_session) -> None:  # type: ignore[no-untyped-def]
    """Integration test: store raw sources, verify dedup by content hash."""
    repo = SourceRepository(db_session)

    result = RawSearchResult(
        uri="https://example.com/test",
        title="Test Page",
        raw_content="This is test content for dedup testing",
        provider_id="test_provider",
    )

    source1, created1 = await repo.create_or_get(result)
    assert created1 is True
    assert source1.uri == "https://example.com/test"

    source2, created2 = await repo.create_or_get(result)
    assert created2 is False
    assert source2.id == source1.id

    result2 = RawSearchResult(
        uri="https://example.com/test2",
        title="Test Page 2",
        raw_content="This is different content",
        provider_id="test_provider",
    )
    source3, created3 = await repo.create_or_get(result2)
    assert created3 is True
    assert source3.id != source1.id
