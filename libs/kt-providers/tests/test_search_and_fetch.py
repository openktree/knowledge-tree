"""Unit tests for the search_and_store helper."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kt_providers.fetcher import FetchResult
from kt_providers.search_and_fetch import search_and_store, store_and_fetch, filter_fresh_urls
from kt_config.types import RawSearchResult


def _make_search_result(uri: str = "https://example.com", title: str = "Test") -> RawSearchResult:
    return RawSearchResult(
        uri=uri,
        title=title,
        raw_content="snippet content",
        provider_id="test",
    )


def _make_write_source(uri: str = "https://example.com", is_full_text: bool = False) -> MagicMock:
    source = MagicMock()
    source.id = uuid.uuid4()
    source.uri = uri
    source.raw_content = "snippet content"
    source.content_hash = "abc123"
    source.is_full_text = is_full_text
    source.is_super_source = False
    source.fetch_attempted = False
    source.content_type = None
    source.provider_metadata = None
    return source


def _make_ctx(content_fetcher: MagicMock | None = None) -> MagicMock:
    """Create a mock AgentContext."""
    ctx = MagicMock()
    ctx.content_fetcher = content_fetcher

    # Write session factory returns a mock session
    mock_write_session = MagicMock()
    mock_write_session.flush = AsyncMock()
    mock_write_session.commit = AsyncMock()
    mock_write_session.rollback = AsyncMock()
    mock_write_session.close = AsyncMock()
    ctx.write_session_factory = MagicMock(return_value=mock_write_session)

    # Provider registry
    ctx.provider_registry = MagicMock()
    ctx.provider_registry.search_all = AsyncMock(return_value=[])

    return ctx


def _mock_settings(**overrides: object) -> MagicMock:
    """Create a mock Settings with defaults."""
    settings = MagicMock()
    settings.page_stale_days = overrides.get("page_stale_days", 30)
    settings.page_fetch_max_extra_pages = overrides.get("page_fetch_max_extra_pages", 3)
    settings.super_source_token_threshold = overrides.get("super_source_token_threshold", 70_000)
    settings.super_source_page_threshold = overrides.get("super_source_page_threshold", 50)
    settings.full_text_fetch_max_urls = overrides.get("full_text_fetch_max_urls", 3)
    settings.full_text_fetch_per_budget_point = overrides.get("full_text_fetch_per_budget_point", 10)
    settings.fetch_guarantee_max_rounds = overrides.get("fetch_guarantee_max_rounds", 4)
    return settings


def _mock_page_log(fresh_urls: set[str] | None = None) -> MagicMock:
    """Create a mock WritePageFetchLogRepository."""
    fresh = fresh_urls or set()
    log = MagicMock()

    async def check_freshness(urls: list[str], stale_days: int = 30) -> dict[str, bool]:
        return {url: url in fresh for url in urls}

    log.check_urls_freshness = AsyncMock(side_effect=check_freshness)
    log.record_fetch = AsyncMock()
    return log


@pytest.mark.asyncio
async def test_search_and_store_no_fetcher():
    """Without a content_fetcher, search_and_store just searches and stores."""
    ctx = _make_ctx(content_fetcher=None)
    results = [_make_search_result("https://a.com"), _make_search_result("https://b.com")]
    ctx.provider_registry.search_all = AsyncMock(return_value=results)

    source_a = _make_write_source("https://a.com")
    source_b = _make_write_source("https://b.com")

    with pytest.MonkeyPatch.context() as mp:
        mock_repo_instance = MagicMock()
        mock_repo_instance.create_or_get = AsyncMock(side_effect=[source_a, source_b])

        mock_repo_cls = MagicMock(return_value=mock_repo_instance)
        mp.setattr("kt_providers.search_and_fetch.WriteSourceRepository", mock_repo_cls)
        mp.setattr("kt_providers.search_and_fetch.WritePageFetchLogRepository", MagicMock(return_value=_mock_page_log()))
        mp.setattr("kt_providers.search_and_fetch.get_settings", lambda: _mock_settings())

        raw_sources = await search_and_store("test query", ctx)

    assert len(raw_sources) == 2


@pytest.mark.asyncio
async def test_search_and_store_with_fetcher_updates_content():
    """With content_fetcher, full-text content is fetched and sources are updated."""
    fetcher = MagicMock()
    full_text = "x" * 200
    fetcher.fetch_urls = AsyncMock(return_value=[
        FetchResult(uri="https://a.com", content=full_text),
    ])

    ctx = _make_ctx(content_fetcher=fetcher)
    results = [_make_search_result("https://a.com")]
    ctx.provider_registry.search_all = AsyncMock(return_value=results)

    source_a = _make_write_source("https://a.com")

    with pytest.MonkeyPatch.context() as mp:
        mock_repo_instance = MagicMock()
        mock_repo_instance.create_or_get = AsyncMock(return_value=source_a)
        mock_repo_instance.update_content = AsyncMock(return_value=True)

        mock_repo_cls = MagicMock(return_value=mock_repo_instance)
        mp.setattr("kt_providers.search_and_fetch.WriteSourceRepository", mock_repo_cls)
        mp.setattr("kt_providers.search_and_fetch.WritePageFetchLogRepository", MagicMock(return_value=_mock_page_log()))
        mp.setattr("kt_providers.search_and_fetch.get_settings", lambda: _mock_settings())

        raw_sources = await search_and_store("test query", ctx)

    assert len(raw_sources) == 1
    mock_repo_instance.update_content.assert_called_once()
    # In-memory object should be updated
    assert source_a.raw_content == full_text
    assert source_a.is_full_text is True


@pytest.mark.asyncio
async def test_search_and_store_fetch_failure_keeps_snippet():
    """When full-text fetch fails, the original snippet is preserved."""
    fetcher = MagicMock()
    fetcher.fetch_urls = AsyncMock(return_value=[
        FetchResult(uri="https://a.com", error="Timeout"),
    ])

    ctx = _make_ctx(content_fetcher=fetcher)
    results = [_make_search_result("https://a.com")]
    ctx.provider_registry.search_all = AsyncMock(return_value=results)

    source_a = _make_write_source("https://a.com")
    original_content = source_a.raw_content

    with pytest.MonkeyPatch.context() as mp:
        mock_repo_instance = MagicMock()
        mock_repo_instance.create_or_get = AsyncMock(return_value=source_a)
        mock_repo_instance.update_content = AsyncMock()

        mock_repo_cls = MagicMock(return_value=mock_repo_instance)
        mp.setattr("kt_providers.search_and_fetch.WriteSourceRepository", mock_repo_cls)
        mp.setattr("kt_providers.search_and_fetch.WritePageFetchLogRepository", MagicMock(return_value=_mock_page_log()))
        mp.setattr("kt_providers.search_and_fetch.get_settings", lambda: _mock_settings())

        raw_sources = await search_and_store("test query", ctx)

    assert len(raw_sources) == 1
    # update_content should NOT have been called since fetch failed
    mock_repo_instance.update_content.assert_not_called()
    assert source_a.raw_content == original_content


@pytest.mark.asyncio
async def test_search_and_store_skips_already_full_text():
    """Sources that are already full_text are not re-fetched."""
    fetcher = MagicMock()
    fetcher.fetch_urls = AsyncMock(return_value=[])

    ctx = _make_ctx(content_fetcher=fetcher)
    results = [_make_search_result("https://a.com")]
    ctx.provider_registry.search_all = AsyncMock(return_value=results)

    source_a = _make_write_source("https://a.com", is_full_text=True)

    with pytest.MonkeyPatch.context() as mp:
        mock_repo_instance = MagicMock()
        mock_repo_instance.create_or_get = AsyncMock(return_value=source_a)

        mock_repo_cls = MagicMock(return_value=mock_repo_instance)
        mp.setattr("kt_providers.search_and_fetch.WriteSourceRepository", mock_repo_cls)
        mp.setattr("kt_providers.search_and_fetch.WritePageFetchLogRepository", MagicMock(return_value=_mock_page_log()))
        mp.setattr("kt_providers.search_and_fetch.get_settings", lambda: _mock_settings())

        raw_sources = await search_and_store("test query", ctx)

    assert len(raw_sources) == 1
    # fetch_urls called with empty list since all sources are already full_text
    fetcher.fetch_urls.assert_not_called()


@pytest.mark.asyncio
async def test_search_and_store_respects_max_fetch_urls():
    """Only max_fetch_urls sources are fetched even if more are available."""
    fetcher = MagicMock()
    full_text = "x" * 200
    fetcher.fetch_urls = AsyncMock(return_value=[
        FetchResult(uri="https://a.com", content=full_text),
        FetchResult(uri="https://b.com", content=full_text),
    ])

    ctx = _make_ctx(content_fetcher=fetcher)
    results = [
        _make_search_result("https://a.com"),
        _make_search_result("https://b.com"),
        _make_search_result("https://c.com"),
    ]
    ctx.provider_registry.search_all = AsyncMock(return_value=results)

    sources = [
        _make_write_source("https://a.com"),
        _make_write_source("https://b.com"),
        _make_write_source("https://c.com"),
    ]

    with pytest.MonkeyPatch.context() as mp:
        mock_repo_instance = MagicMock()
        mock_repo_instance.create_or_get = AsyncMock(
            side_effect=sources
        )
        mock_repo_instance.update_content = AsyncMock(return_value=True)

        mock_repo_cls = MagicMock(return_value=mock_repo_instance)
        mp.setattr("kt_providers.search_and_fetch.WriteSourceRepository", mock_repo_cls)
        mp.setattr("kt_providers.search_and_fetch.WritePageFetchLogRepository", MagicMock(return_value=_mock_page_log()))
        mp.setattr("kt_providers.search_and_fetch.get_settings", lambda: _mock_settings())

        raw_sources = await search_and_store("test query", ctx, max_fetch_urls=2)

    # fetch_urls should have been called with only 2 URLs
    call_args = fetcher.fetch_urls.call_args
    fetched_uris = call_args[0][0]
    assert len(fetched_uris) == 2


@pytest.mark.asyncio
async def test_search_and_store_empty_results():
    """Empty search results return empty list."""
    ctx = _make_ctx(content_fetcher=None)
    ctx.provider_registry.search_all = AsyncMock(return_value=[])

    with pytest.MonkeyPatch.context() as mp:
        mock_repo_cls = MagicMock()
        mp.setattr("kt_providers.search_and_fetch.WriteSourceRepository", mock_repo_cls)
        mp.setattr("kt_providers.search_and_fetch.WritePageFetchLogRepository", MagicMock(return_value=_mock_page_log()))
        mp.setattr("kt_providers.search_and_fetch.get_settings", lambda: _mock_settings())

        raw_sources = await search_and_store("test query", ctx)

    assert raw_sources == []


# --- Tests for page-level deduplication ---


@pytest.mark.asyncio
async def testfilter_fresh_urls_removes_fresh():
    """Fresh URLs are filtered out."""
    page_log = _mock_page_log(fresh_urls={"https://a.com", "https://c.com"})

    results = [
        _make_search_result("https://a.com"),
        _make_search_result("https://b.com"),
        _make_search_result("https://c.com"),
    ]

    filtered, skipped = await filter_fresh_urls(results, page_log, stale_days=30)

    assert len(filtered) == 1
    assert filtered[0].uri == "https://b.com"
    assert skipped == 2


@pytest.mark.asyncio
async def testfilter_fresh_urls_no_fresh():
    """When nothing is fresh, all results pass through."""
    page_log = _mock_page_log(fresh_urls=set())

    results = [
        _make_search_result("https://a.com"),
        _make_search_result("https://b.com"),
    ]

    filtered, skipped = await filter_fresh_urls(results, page_log, stale_days=30)

    assert len(filtered) == 2
    assert skipped == 0


@pytest.mark.asyncio
async def testfilter_fresh_urls_empty_input():
    """Empty input returns empty output."""
    page_log = _mock_page_log()
    filtered, skipped = await filter_fresh_urls([], page_log, stale_days=30)
    assert filtered == []
    assert skipped == 0


@pytest.mark.asyncio
async def test_search_and_store_skips_fresh_urls():
    """Fresh URLs from write_page_fetch_log are excluded from results."""
    ctx = _make_ctx(content_fetcher=None)
    results = [
        _make_search_result("https://fresh.com"),
        _make_search_result("https://new.com"),
    ]
    ctx.provider_registry.search_all = AsyncMock(return_value=results)

    source_new = _make_write_source("https://new.com")

    with pytest.MonkeyPatch.context() as mp:
        mock_repo_instance = MagicMock()
        mock_repo_instance.create_or_get = AsyncMock(return_value=source_new)

        mock_repo_cls = MagicMock(return_value=mock_repo_instance)
        mp.setattr("kt_providers.search_and_fetch.WriteSourceRepository", mock_repo_cls)

        page_log = _mock_page_log(fresh_urls={"https://fresh.com"})
        mp.setattr("kt_providers.search_and_fetch.WritePageFetchLogRepository", MagicMock(return_value=page_log))
        mp.setattr("kt_providers.search_and_fetch.get_settings", lambda: _mock_settings())

        raw_sources = await search_and_store("test query", ctx)

    # Only the non-fresh URL should be stored
    assert len(raw_sources) == 1
    assert raw_sources[0].uri == "https://new.com"


@pytest.mark.asyncio
async def test_search_and_store_records_fetches_in_log():
    """Processed URLs are recorded in the page fetch log."""
    ctx = _make_ctx(content_fetcher=None)
    results = [_make_search_result("https://a.com")]
    ctx.provider_registry.search_all = AsyncMock(return_value=results)

    source_a = _make_write_source("https://a.com")

    with pytest.MonkeyPatch.context() as mp:
        mock_repo_instance = MagicMock()
        mock_repo_instance.create_or_get = AsyncMock(return_value=source_a)

        mock_repo_cls = MagicMock(return_value=mock_repo_instance)
        mp.setattr("kt_providers.search_and_fetch.WriteSourceRepository", mock_repo_cls)

        page_log = _mock_page_log()
        mp.setattr("kt_providers.search_and_fetch.WritePageFetchLogRepository", MagicMock(return_value=page_log))
        mp.setattr("kt_providers.search_and_fetch.get_settings", lambda: _mock_settings())

        await search_and_store("test query", ctx)

    # record_fetch should have been called for the processed URL
    page_log.record_fetch.assert_called_once()
    call_kwargs = page_log.record_fetch.call_args
    assert call_kwargs[1]["url"] == "https://a.com" or call_kwargs[0][0] == "https://a.com"
