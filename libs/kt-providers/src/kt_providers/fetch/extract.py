"""Content-type-aware extraction helpers shared by all fetch providers.

Each provider is responsible for actually fetching bytes off the network
(possibly via TLS impersonation, a headless browser, etc.).  Once the bytes
are in hand, the *interpretation* of those bytes — extracting plain text
from HTML/PDF, surfacing image metadata, recognising "non-text" content
types — is identical regardless of how we got them.  Centralising it here
prevents drift between providers.

Public surface:
    classify_content_type(ct) -> "text" | "pdf" | "image" | "unknown"
    extract_html(uri, raw_html, content_type) -> FetchResult
    extract_pdf(uri, pdf_bytes, content_type) -> FetchResult
    extract_image(uri, image_bytes, content_type) -> FetchResult
    extract_text(uri, text, content_type) -> FetchResult       # plain text/* fallback
"""

from __future__ import annotations

import logging

import trafilatura  # type: ignore[import-untyped]

from kt_providers.fetch.types import MIN_EXTRACTED_LENGTH, FetchResult

logger = logging.getLogger(__name__)


def classify_content_type(content_type: str) -> str:
    """Classify a content-type header into one of {text, pdf, image, unknown}."""
    ct = content_type.lower()
    if "application/pdf" in ct:
        return "pdf"
    if ct.startswith("image/") or "image/" in ct:
        return "image"
    if "text/" in ct or "html" in ct or "json" in ct or "xml" in ct:
        return "text"
    return "unknown"


def extract_html(uri: str, raw_html: str, content_type: str) -> FetchResult:
    """Run trafilatura over an HTML body and wrap the result.

    Returns a `FetchResult` with `error="Extraction produced insufficient content"`
    when trafilatura returns nothing meaningful — that lets the registry's
    fallback chain try the next provider, which is exactly what we want when
    a site serves a JS bot-challenge page that contains no real article body.
    """
    extracted = trafilatura.extract(
        raw_html,
        favor_recall=True,
        include_comments=False,
        include_tables=True,
    )
    if not extracted or len(extracted) < MIN_EXTRACTED_LENGTH:
        return FetchResult(
            uri=uri,
            error="Extraction produced insufficient content",
            content_type=content_type,
        )

    return FetchResult(
        uri=uri,
        content=extracted,
        content_type=content_type,
        html_metadata=extract_html_metadata(raw_html),
    )


def extract_text(uri: str, text: str, content_type: str) -> FetchResult:
    """Wrap a plain-text body, applying the same minimum-length guard."""
    if len(text) < MIN_EXTRACTED_LENGTH:
        return FetchResult(uri=uri, error="Content too short", content_type=content_type)
    return FetchResult(uri=uri, content=text, content_type=content_type)


def extract_pdf(uri: str, pdf_bytes: bytes, content_type: str) -> FetchResult:
    """Extract text + metadata from a PDF byte stream via pymupdf + kt_facts."""
    try:
        from kt_facts.processing.file_processing import extract_text_from_pdf  # type: ignore[import-not-found]

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
        if not extracted or len(extracted) < MIN_EXTRACTED_LENGTH:
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


def extract_image(uri: str, image_bytes: bytes, content_type: str) -> FetchResult:
    """Wrap raw image bytes for downstream multimodal processing."""
    if not image_bytes:
        return FetchResult(uri=uri, error="Empty image response", content_type=content_type)
    return FetchResult(
        uri=uri,
        content="[Image content — requires multimodal extraction]",
        content_type=content_type,
        raw_bytes=image_bytes,
    )


def extract_html_metadata(raw_html: str) -> dict[str, str | None] | None:
    """Pull author/sitename/date/title from HTML via trafilatura.metadata."""
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
