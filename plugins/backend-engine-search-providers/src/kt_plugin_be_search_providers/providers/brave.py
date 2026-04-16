import asyncio

import httpx
import structlog

from kt_core_engine_api.search import KnowledgeProvider, RawSearchResult

logger = structlog.get_logger(__name__)

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_BASE_DELAY = 1.0  # seconds


class BraveSearchProvider(KnowledgeProvider):
    """Brave Search API provider."""

    SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._client = httpx.AsyncClient(timeout=30.0)

    @property
    def provider_id(self) -> str:
        return "brave_search"

    async def search(self, query: str, max_results: int = 10) -> list[RawSearchResult]:
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self._api_key,
        }
        params = {
            "q": query,
            "count": min(max_results, 20),
        }

        last_exc: httpx.HTTPStatusError | None = None
        for attempt in range(_MAX_RETRIES):
            response = await self._client.get(self.SEARCH_URL, headers=headers, params=params)
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
                "brave_search_retrying",
                status=response.status_code,
                attempt=attempt + 1,
                delay=delay,
                query=query,
            )
            await asyncio.sleep(delay)
        else:
            raise last_exc  # type: ignore[misc]

        data = response.json()
        results: list[RawSearchResult] = []

        web_results = data.get("web", {}).get("results", [])
        for item in web_results[:max_results]:
            content_parts: list[str] = []
            if item.get("description"):
                content_parts.append(item["description"])
            if item.get("extra_snippets"):
                content_parts.extend(item["extra_snippets"])

            raw_content = "\n\n".join(content_parts) if content_parts else ""

            results.append(
                RawSearchResult(
                    uri=item.get("url", ""),
                    title=item.get("title", ""),
                    raw_content=raw_content,
                    provider_id=self.provider_id,
                    provider_metadata={
                        "age": item.get("age"),
                        "language": item.get("language"),
                        "family_friendly": item.get("family_friendly"),
                    },
                )
            )

        return results

    async def is_available(self) -> bool:
        return bool(self._api_key)

    async def close(self) -> None:
        await self._client.aclose()
