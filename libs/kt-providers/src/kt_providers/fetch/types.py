"""Shared dataclasses for the fetch provider package.

`FetchResult` is the canonical return value of every `ContentFetcherProvider`
implementation.  It carries either the extracted text content or an error
description, plus an audit trail of all strategies that were tried (the
`attempts` list) and the id of the strategy that ultimately succeeded
(`provider_id`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Minimum extracted-text length we consider "real" content.  Anything shorter
# is treated as failure ("insufficient content"); a few publishers serve a
# bot-challenge interstitial of a couple hundred bytes which trafilatura then
# strips down to nothing — those should fall through to the next provider.
MIN_EXTRACTED_LENGTH = 50


@dataclass
class FetchAttempt:
    """A single provider attempt within a `FetchProviderRegistry.fetch()` call.

    Recorded for *every* provider tried — successful or not — so the UI can
    show "tried httpx → curl_cffi → flaresolverr" and operators can later
    audit which strategies actually unblock which hosts.
    """

    provider_id: str
    success: bool
    error: str | None
    elapsed_ms: int

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "success": self.success,
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
        }


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

    # Strategy audit — populated by the registry, not by individual providers.
    provider_id: str | None = None  # id of the winning provider (if any)
    attempts: list[FetchAttempt] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.content is not None and len(self.content) >= MIN_EXTRACTED_LENGTH

    @property
    def is_image(self) -> bool:
        """True when the fetched content is an image (bytes stored in raw_bytes)."""
        return self.content_type is not None and self.content_type.startswith("image/")
