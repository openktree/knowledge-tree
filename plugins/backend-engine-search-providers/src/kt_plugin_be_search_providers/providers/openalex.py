"""OpenAlex scholarly search provider.

OpenAlex (https://openalex.org) is a free, open catalog of scholarly works.
Requests pass through the "polite pool" by appending the operator's email
address as the ``mailto`` query parameter — no API key required.
"""

from __future__ import annotations

import asyncio

import httpx
import structlog

from kt_core_engine_api.search import KnowledgeProvider, RawSearchResult

logger = structlog.get_logger(__name__)

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_BASE_DELAY = 1.0  # seconds


def reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str:
    """Invert OpenAlex's ``abstract_inverted_index`` back into plain text."""
    if not inverted_index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, idxs in inverted_index.items():
        for idx in idxs:
            positions.append((idx, word))
    positions.sort(key=lambda p: p[0])
    return " ".join(word for _, word in positions)


class OpenAlexSearchProvider(KnowledgeProvider):
    """OpenAlex Works API provider — scholarly search via https://api.openalex.org."""

    SEARCH_URL = "https://api.openalex.org/works"

    def __init__(self, mailto: str | None = None) -> None:
        self._mailto = mailto or None
        self._client = httpx.AsyncClient(timeout=30.0)

    @property
    def provider_id(self) -> str:
        return "openalex"

    async def search(self, query: str, max_results: int = 10) -> list[RawSearchResult]:
        params: dict[str, str | int] = {
            "search": query,
            "per-page": min(max(max_results, 1), 25),
        }
        if self._mailto:
            params["mailto"] = self._mailto

        last_exc: httpx.HTTPStatusError | None = None
        for attempt in range(_MAX_RETRIES):
            response = await self._client.get(self.SEARCH_URL, params=params)
            if response.status_code not in _RETRYABLE_STATUS_CODES:
                response.raise_for_status()
                break
            last_exc = httpx.HTTPStatusError(
                f"{response.status_code} {response.reason_phrase}",
                request=response.request,
                response=response,
            )
            delay = _BASE_DELAY * (2**attempt)
            logger.warning(
                "openalex_search_retrying",
                status=response.status_code,
                attempt=attempt + 1,
                delay=delay,
                query=query,
            )
            await asyncio.sleep(delay)
        else:
            raise last_exc  # type: ignore[misc]

        data = response.json()
        works = data.get("results", [])
        out: list[RawSearchResult] = []

        for work in works[:max_results]:
            doi = work.get("doi")
            openalex_id = work.get("id", "")
            uri = doi or openalex_id
            if not uri:
                continue

            title = work.get("title") or work.get("display_name") or ""
            abstract = reconstruct_abstract(work.get("abstract_inverted_index"))

            metadata: dict[str, object] = {"openalex_id": openalex_id}
            if work.get("publication_year") is not None:
                metadata["publication_year"] = work["publication_year"]
            if work.get("cited_by_count") is not None:
                metadata["cited_by_count"] = work["cited_by_count"]
            if work.get("type"):
                metadata["type"] = work["type"]
            authorships = work.get("authorships") or []
            if authorships:
                metadata["authors"] = [
                    a.get("author", {}).get("display_name")
                    for a in authorships
                    if a.get("author", {}).get("display_name")
                ]
            oa = work.get("open_access")
            if oa:
                metadata["open_access"] = {
                    "is_oa": oa.get("is_oa"),
                    "oa_url": oa.get("oa_url"),
                }

            out.append(
                RawSearchResult(
                    uri=uri,
                    title=title,
                    raw_content=abstract,
                    provider_id=self.provider_id,
                    provider_metadata=metadata,
                )
            )

        return out

    async def is_available(self) -> bool:
        params: dict[str, str | int] = {"per-page": 1}
        if self._mailto:
            params["mailto"] = self._mailto
        try:
            response = await self._client.get(self.SEARCH_URL, params=params)
        except httpx.HTTPError:
            return False
        return 200 <= response.status_code < 300

    async def close(self) -> None:
        await self._client.aclose()
