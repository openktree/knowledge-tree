import asyncio

import httpx
import structlog

from kt_providers.base import KnowledgeProvider
from kt_config.types import RawSearchResult

logger = structlog.get_logger(__name__)

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_BASE_DELAY = 1.0  # seconds


class SerperSearchProvider(KnowledgeProvider):
    """Serper.dev (Google Search) API provider."""

    SEARCH_URL = "https://google.serper.dev/search"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._client = httpx.AsyncClient(timeout=30.0)

    @property
    def provider_id(self) -> str:
        return "serper"

    async def search(self, query: str, max_results: int = 10) -> list[RawSearchResult]:
        headers = {
            "X-API-KEY": self._api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "q": query,
            "num": min(max_results, 20),
        }

        last_exc: httpx.HTTPStatusError | None = None
        for attempt in range(_MAX_RETRIES):
            response = await self._client.post(self.SEARCH_URL, headers=headers, json=payload)
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
                "serper_search_retrying",
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

        organic = data.get("organic", [])
        for item in organic[:max_results]:
            # Title is stored separately — excluding it from raw_content
            # prevents the extraction LLM from treating titles as facts
            content_parts: list[str] = []
            if item.get("snippet"):
                content_parts.append(item["snippet"])

            raw_content = "\n\n".join(content_parts) if content_parts else ""

            metadata: dict[str, object] = {}
            if item.get("position") is not None:
                metadata["position"] = item["position"]
            if item.get("date"):
                metadata["date"] = item["date"]

            results.append(
                RawSearchResult(
                    uri=item.get("link", ""),
                    title=item.get("title", ""),
                    raw_content=raw_content,
                    provider_id=self.provider_id,
                    provider_metadata=metadata if metadata else None,
                )
            )

        return results

    async def is_available(self) -> bool:
        return bool(self._api_key)

    async def close(self) -> None:
        await self._client.aclose()
