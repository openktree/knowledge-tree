"""ContentFetcher — fetch full-text content from URLs and extract readable text."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import httpx
import trafilatura  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

_MIN_EXTRACTED_LENGTH = 50
_DEFAULT_TIMEOUT = 15.0
_DEFAULT_MAX_CONCURRENT = 3


@dataclass
class FetchResult:
    """Result of fetching and extracting text from a URL."""

    uri: str
    content: str | None = None
    error: str | None = None
    content_type: str | None = None
    raw_bytes: bytes | None = field(default=None, repr=False)
    page_count: int | None = None  # number of pages (PDFs only)
    pdf_metadata: dict[str, str] | None = None  # pymupdf doc.metadata for PDFs
    html_metadata: dict[str, str | None] | None = None  # trafilatura page metadata

    @property
    def success(self) -> bool:
        return self.content is not None and len(self.content) >= _MIN_EXTRACTED_LENGTH

    @property
    def is_image(self) -> bool:
        """True when the fetched content is an image (bytes stored in raw_bytes)."""
        return self.content_type is not None and self.content_type.startswith("image/")


class FileDataStore:
    """Ephemeral in-memory store for binary file data keyed by URI.

    Used to pass image/PDF bytes from the fetch stage to the decomposition
    pipeline without persisting large blobs in the database.
    """

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    def store(self, uri: str, data: bytes) -> None:
        self._data[uri] = data

    def get(self, uri: str) -> bytes | None:
        return self._data.get(uri)

    def remove(self, uri: str) -> None:
        self._data.pop(uri, None)

    def clear(self) -> None:
        self._data.clear()

    def has(self, uri: str) -> bool:
        return uri in self._data


class ContentFetcher:
    """Fetches URLs and extracts readable text content using trafilatura."""

    def __init__(
        self,
        timeout: float = _DEFAULT_TIMEOUT,
        max_concurrent: int = _DEFAULT_MAX_CONCURRENT,
    ) -> None:
        self._timeout = timeout
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            from kt_config.settings import get_settings

            settings = get_settings()
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                follow_redirects=True,
                headers={
                    "User-Agent": settings.fetch_user_agent,
                },
            )
        return self._client

    async def fetch_url(self, uri: str) -> FetchResult:
        """Fetch a URL and extract readable text content.

        Returns FetchResult with extracted text or error description.
        Handles text/HTML, PDF (via pymupdf), and image content types.
        """
        async with self._semaphore:
            try:
                client = await self._get_client()
                response = await client.get(uri)
                response.raise_for_status()

                content_type = response.headers.get("content-type", "")
                ct_lower = content_type.lower()
                classified = _classify_content_type(ct_lower)

                if classified == "pdf":
                    return await self._handle_pdf(uri, response, content_type)
                elif classified == "image":
                    return self._handle_image(uri, response, content_type)
                elif classified == "text":
                    return self._handle_text(uri, response, content_type)
                else:
                    return FetchResult(
                        uri=uri,
                        error=f"Non-text content type: {content_type}",
                        content_type=content_type,
                    )

            except httpx.TimeoutException:
                return FetchResult(uri=uri, error="Timeout")
            except httpx.HTTPStatusError as e:
                return FetchResult(uri=uri, error=f"HTTP {e.response.status_code}")
            except Exception as e:
                logger.debug("Error fetching %s: %s", uri, e)
                return FetchResult(uri=uri, error=str(e))

    async def _handle_pdf(self, uri: str, response: httpx.Response, content_type: str) -> FetchResult:
        """Extract text from a PDF response using pymupdf."""
        try:
            from kt_facts.processing.file_processing import extract_text_from_pdf

            pdf_bytes = response.content

            # Extract PDF metadata (author, producer, title, etc.)
            pdf_meta: dict[str, str] | None = None
            page_count: int | None = None
            try:
                import pymupdf  # type: ignore[import-untyped]

                with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
                    pdf_meta = dict(doc.metadata) if doc.metadata else None
                    page_count = len(doc)
            except Exception:
                logger.debug("Failed to extract PDF metadata for %s", uri)

            extracted = extract_text_from_pdf(pdf_bytes)
            if not extracted or len(extracted) < _MIN_EXTRACTED_LENGTH:
                return FetchResult(
                    uri=uri,
                    error="PDF extraction produced insufficient content",
                    content_type=content_type,
                    page_count=page_count,
                    pdf_metadata=pdf_meta,
                )
            return FetchResult(
                uri=uri,
                content=extracted,
                content_type=content_type,
                page_count=page_count,
                pdf_metadata=pdf_meta,
            )
        except Exception as e:
            logger.debug("PDF extraction failed for %s: %s", uri, e)
            return FetchResult(uri=uri, error=f"PDF extraction error: {e}", content_type=content_type)

    def _handle_image(self, uri: str, response: httpx.Response, content_type: str) -> FetchResult:
        """Store raw image bytes for later multimodal processing."""
        image_bytes = response.content
        if not image_bytes:
            return FetchResult(uri=uri, error="Empty image response", content_type=content_type)
        return FetchResult(
            uri=uri,
            content="[Image content — requires multimodal extraction]",
            content_type=content_type,
            raw_bytes=image_bytes,
        )

    def _handle_text(self, uri: str, response: httpx.Response, content_type: str) -> FetchResult:
        """Extract text from HTML or plain text responses."""
        raw_html = response.text
        ct_lower = content_type.lower()

        if "html" in ct_lower:
            extracted = trafilatura.extract(
                raw_html,
                favor_recall=True,
                include_comments=False,
                include_tables=True,
            )
            if not extracted or len(extracted) < _MIN_EXTRACTED_LENGTH:
                return FetchResult(
                    uri=uri,
                    error="Extraction produced insufficient content",
                    content_type=content_type,
                )

            # Extract page metadata (author, sitename, etc.) from HTML
            html_meta = _extract_html_metadata(raw_html)

            return FetchResult(
                uri=uri,
                content=extracted,
                content_type=content_type,
                html_metadata=html_meta,
            )
        else:
            # Plain text — return as-is
            if len(raw_html) < _MIN_EXTRACTED_LENGTH:
                return FetchResult(uri=uri, error="Content too short", content_type=content_type)
            return FetchResult(uri=uri, content=raw_html, content_type=content_type)

    async def fetch_urls(self, uris: list[str]) -> list[FetchResult]:
        """Fetch multiple URLs concurrently (respecting semaphore limit)."""
        tasks = [self.fetch_url(uri) for uri in uris]
        return list(await asyncio.gather(*tasks))

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


def _extract_html_metadata(raw_html: str) -> dict[str, str | None] | None:
    """Extract structured metadata from HTML using trafilatura.

    Returns a dict with keys like 'author', 'sitename', 'date', 'title',
    or None if extraction fails or yields nothing useful.
    """
    try:
        meta = trafilatura.metadata.extract_metadata(raw_html)
        if meta is None:
            return None

        result: dict[str, str | None] = {}
        for key in ("author", "sitename", "date", "title", "categories", "tags"):
            value = getattr(meta, key, None)
            if value:
                result[key] = str(value)

        return result if result else None
    except Exception:
        logger.debug("Failed to extract HTML metadata", exc_info=True)
        return None


def _classify_content_type(content_type: str) -> str:
    """Classify a content-type header into a category.

    Returns one of: "text", "pdf", "image", "unknown".
    """
    ct = content_type.lower()
    if "application/pdf" in ct:
        return "pdf"
    if ct.startswith("image/") or "image/" in ct:
        return "image"
    if "text/" in ct or "html" in ct or "json" in ct or "xml" in ct:
        return "text"
    return "unknown"
