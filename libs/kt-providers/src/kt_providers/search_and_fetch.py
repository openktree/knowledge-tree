"""Centralized search -> store -> optional full-text fetch helper.

Replaces the repeated pattern of search_all -> create_or_get across tool files.
Includes page-level deduplication: URLs recently fetched and processed are
skipped, and additional search pages are loaded to backfill.

All database writes target the write-db only.  The sync worker propagates
sources to graph-db.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from kt_agents_core.state import AgentContext
from kt_config.settings import get_settings
from kt_config.types import RawSearchResult
from kt_db.repositories.write_page_fetch_log import WritePageFetchLogRepository
from kt_db.repositories.write_sources import WriteSourceRepository
from kt_db.write_models import WriteRawSource

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def search_and_store(
    query: str,
    ctx: AgentContext,
    max_results: int = 5,
    max_fetch_urls: int | None = None,
    write_session: AsyncSession | None = None,
) -> list[WriteRawSource]:
    """Search providers, store results, optionally fetch full page content.

    1. Search all registered providers for the query.
    2. Filter out URLs that were recently processed (write_page_fetch_log).
    3. If too many were skipped, paginate to get replacement URLs.
    4. Deduplicate and store each result as a WriteRawSource.
    5. If ctx.content_fetcher is available, fetch full page content for the
       top N URLs and update the stored WriteRawSource records.
    6. Record successful fetches in write_page_fetch_log.

    Args:
        query: The search query string.
        ctx: AgentContext with provider_registry, session, and optional content_fetcher.
        max_results: Maximum results per provider.
        max_fetch_urls: Maximum URLs to fetch full content for.
        write_session: Optional write-db session.  If provided, caller manages
            commit/close.  If None, a session is created from ctx.write_session_factory.

    Returns:
        List of WriteRawSource objects (with full text if fetching succeeded).
    """
    settings = get_settings()
    stale_days = settings.page_stale_days
    max_extra_pages = settings.page_fetch_max_extra_pages

    owns_session = write_session is None
    if write_session is None:
        if ctx.write_session_factory is None:
            raise RuntimeError("write_session_factory required for search_and_store")
        write_session = ctx.write_session_factory()

    page_log = WritePageFetchLogRepository(write_session)

    try:
        raw_results = await ctx.provider_registry.search_all(query, max_results=max_results)

        # Filter out fresh (recently processed) URLs
        filtered, skipped_count = await filter_fresh_urls(raw_results, page_log, stale_days)

        # If we skipped URLs, try to backfill by requesting more results
        if skipped_count > 0 and max_extra_pages > 0:
            needed = skipped_count
            pages_tried = 0

            while needed > 0 and pages_tried < max_extra_pages:
                extra_results = await ctx.provider_registry.search_all(query, max_results=max_results)
                seen_uris = {r.uri for r in raw_results}
                new_results = [r for r in extra_results if r.uri not in seen_uris]

                if not new_results:
                    break

                new_filtered, _new_skipped = await filter_fresh_urls(new_results, page_log, stale_days)
                filtered.extend(new_filtered)
                needed -= len(new_filtered)
                pages_tried += 1

        sources = await store_and_fetch(
            filtered,
            ctx,
            max_fetch_urls=max_fetch_urls,
            write_session=write_session,
        )

        # Record fetched URLs in the page log
        for source in sources:
            if source.uri:
                await page_log.record_fetch(
                    url=source.uri,
                    raw_source_id=source.id,
                    content_hash=source.content_hash,
                )
        await write_session.flush()

        if owns_session:
            await write_session.commit()

        return sources
    except Exception:
        if owns_session:
            try:
                await write_session.rollback()
            except Exception:
                pass
        raise
    finally:
        if owns_session:
            await write_session.close()


async def filter_fresh_urls(
    results: list[RawSearchResult],
    page_log: WritePageFetchLogRepository,
    stale_days: int,
) -> tuple[list[RawSearchResult], int]:
    """Remove results whose URLs are still fresh in the page fetch log.

    Returns (filtered_results, skipped_count).
    """
    if not results:
        return [], 0

    urls = [r.uri for r in results]
    freshness = await page_log.check_urls_freshness(urls, stale_days)

    filtered: list[RawSearchResult] = []
    skipped = 0
    for result in results:
        if freshness.get(result.uri, False):
            logger.debug("Skipping fresh URL: %s", result.uri)
            skipped += 1
        else:
            filtered.append(result)

    if skipped:
        logger.info(
            "Skipped %d fresh URLs out of %d search results",
            skipped,
            len(results),
        )

    return filtered, skipped


async def store_and_fetch(
    raw_results: list[RawSearchResult],
    ctx: AgentContext,
    max_fetch_urls: int | None = None,
    write_session: AsyncSession | None = None,
) -> list[WriteRawSource]:
    """Store pre-fetched RawSearchResult objects and optionally fetch full text.

    All writes go to write-db only.  The sync worker propagates to graph-db.

    Args:
        raw_results: Pre-fetched search results to store.
        ctx: AgentContext with optional content_fetcher.
        max_fetch_urls: Maximum URLs to fetch full content for.
            Defaults to ``settings.full_text_fetch_max_urls`` when *None*.
        write_session: Optional write-db session.  If provided, caller manages
            commit/close.  If None, a session is created from ctx.write_session_factory.

    Returns:
        List of WriteRawSource objects (with full text if fetching succeeded).
    """
    if max_fetch_urls is None:
        max_fetch_urls = get_settings().full_text_fetch_max_urls

    owns_session = write_session is None
    if write_session is None:
        if ctx.write_session_factory is None:
            raise RuntimeError("write_session_factory required for store_and_fetch")
        write_session = ctx.write_session_factory()

    source_repo = WriteSourceRepository(write_session)
    raw_sources: list[WriteRawSource] = []

    try:
        for result in raw_results:
            source = await source_repo.create_or_get(
                uri=result.uri,
                title=result.title,
                raw_content=result.raw_content,
                provider_id=result.provider_id,
                provider_metadata=result.provider_metadata,
            )
            raw_sources.append(source)

        if not raw_sources or ctx.content_fetcher is None:
            if owns_session:
                await write_session.commit()
            return raw_sources

        # Fetch full-text content for top N URLs that aren't already full-text
        urls_to_fetch: list[tuple[int, str]] = []
        for i, source in enumerate(raw_sources):
            if source.is_full_text:
                continue
            if source.uri and len(urls_to_fetch) < max_fetch_urls:
                urls_to_fetch.append((i, source.uri))

        if not urls_to_fetch:
            if owns_session:
                await write_session.commit()
            return raw_sources

        uris = [uri for _, uri in urls_to_fetch]
        fetch_results = await ctx.content_fetcher.fetch_urls(uris)

        # Pre-compute super source thresholds once
        _ss_settings = get_settings()
        _ss_char_threshold = _ss_settings.super_source_token_threshold * 4
        _ss_page_threshold = _ss_settings.super_source_page_threshold

        for (idx, _uri), fetch_result in zip(urls_to_fetch, fetch_results):
            source = raw_sources[idx]
            source.fetch_attempted = True
            source.fetch_error = fetch_result.error if not fetch_result.success else None

            # Store PDF/HTML metadata in provider_metadata for author extraction
            if fetch_result.pdf_metadata or fetch_result.html_metadata:
                existing_meta = source.provider_metadata or {}
                if fetch_result.pdf_metadata:
                    existing_meta["pdf_metadata"] = fetch_result.pdf_metadata
                if fetch_result.html_metadata:
                    existing_meta["html_metadata"] = fetch_result.html_metadata
                source.provider_metadata = existing_meta

            # ── Super source detection ──────────────────────────────────
            is_super = False

            if fetch_result.content and len(fetch_result.content) > _ss_char_threshold:
                is_super = True
            if fetch_result.page_count and fetch_result.page_count > _ss_page_threshold:
                is_super = True

            if is_super:
                source.is_super_source = True
                logger.info(
                    "Super source detected: %s (~%d tokens, %s pages)",
                    source.uri,
                    len(fetch_result.content or "") // 4,
                    fetch_result.page_count or "n/a",
                )

            if fetch_result.is_image and fetch_result.raw_bytes:
                # Store image bytes in file_data_store for multimodal extraction
                ctx.file_data_store.store(source.uri, fetch_result.raw_bytes)
                placeholder = fetch_result.content or "[Image content]"
                try:
                    updated = await source_repo.update_content(
                        source.id,
                        placeholder,
                        is_full_text=True,
                        content_type=fetch_result.content_type,
                    )
                    if updated:
                        source.content_type = fetch_result.content_type
                        source.raw_content = placeholder
                        source.is_full_text = True
                    else:
                        logger.debug(
                            "Skipped update for source %s: content already stored under another source",
                            source.id,
                        )
                except Exception:
                    logger.debug(
                        "Failed to update source %s with image placeholder",
                        source.id,
                        exc_info=True,
                    )
            elif fetch_result.success:
                assert fetch_result.content is not None
                try:
                    updated = await source_repo.update_content(
                        source.id,
                        fetch_result.content,
                        is_full_text=True,
                        content_type=fetch_result.content_type,
                    )
                    if updated:
                        source.raw_content = fetch_result.content
                        source.is_full_text = True
                        if fetch_result.content_type:
                            source.content_type = fetch_result.content_type
                    else:
                        logger.debug(
                            "Skipped update for source %s: content already stored under another source",
                            source.id,
                        )
                except Exception:
                    logger.debug(
                        "Failed to update source %s with full text: %s",
                        source.id,
                        fetch_result.uri,
                        exc_info=True,
                    )
            else:
                logger.debug(
                    "Full-text fetch failed for %s: %s",
                    fetch_result.uri,
                    fetch_result.error,
                )

        if owns_session:
            await write_session.commit()

        return raw_sources
    except Exception:
        if owns_session:
            try:
                await write_session.rollback()
            except Exception:
                pass
        raise
    finally:
        if owns_session:
            await write_session.close()
