"""Source-level author extraction strategies.

Extracts person (byline) and organization (publisher) metadata from sources.
Runs once per source — all facts from that source inherit the same authors.

Two strategies:
  1. PdfMetadataStrategy — reads pymupdf doc.metadata (free, instant, PDF-only)
  2. LlmHeaderStrategy  — sends first ~500 chars + URL to a cheap LLM
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kt_models.gateway import ModelGateway

logger = logging.getLogger(__name__)

# Known junk values from PDF producers/creators — not real authors
_PDF_JUNK_AUTHORS = {
    "latex with hyperref",
    "pdftex",
    "microsoft word",
    "microsoft® word",
    "adobe indesign",
    "adobe acrobat",
    "google docs",
    "libreoffice",
    "openoffice",
    "prince",
    "wkhtmltopdf",
    "chrome",
    "firefox",
    "safari",
    "pdfium",
}


@dataclass
class AuthorInfo:
    """Source-level author metadata."""

    person: str | None = None  # "Emma Roth", "Ashish Vaswani, Noam Shazeer"
    organization: str | None = None  # "BBC", "Google Brain", "Wikipedia"


@dataclass
class SourceContext:
    """Everything an author strategy might need."""

    url: str
    header_text: str  # first ~500 chars of content
    pdf_metadata: dict[str, Any] | None = None  # pymupdf doc.metadata (PDFs only)
    html_metadata: dict[str, str | None] | None = None  # trafilatura page metadata


class AuthorStrategy(ABC):
    """Base class for source-level author extraction."""

    @abstractmethod
    async def extract(self, ctx: SourceContext) -> AuthorInfo | None:
        """Extract author info from the source context. Returns None if unable."""
        ...


class PdfMetadataStrategy(AuthorStrategy):
    """Extract author from pymupdf doc.metadata — free, instant, PDF-only."""

    async def extract(self, ctx: SourceContext) -> AuthorInfo | None:
        if not ctx.pdf_metadata:
            return None

        raw_author = (ctx.pdf_metadata.get("author") or "").strip()
        raw_producer = (ctx.pdf_metadata.get("producer") or "").strip()
        raw_creator = (ctx.pdf_metadata.get("creator") or "").strip()

        person = _clean_person_field(self._clean_value(raw_author))
        # Use producer as organization, falling back to creator
        organization = self._clean_value(raw_producer) or self._clean_value(raw_creator)

        if not person and not organization:
            return None
        return AuthorInfo(person=person, organization=organization)

    @staticmethod
    def _clean_value(value: str) -> str | None:
        """Return None for empty or known-junk values."""
        if not value:
            return None
        # Check against known junk patterns
        lower = value.lower().strip()
        for junk in _PDF_JUNK_AUTHORS:
            if lower.startswith(junk):
                return None
        # Filter version strings like "pdfTeX-1.40.25"
        if re.match(r"^[a-zA-Z]+-[\d.]+$", value):
            return None
        return value


_LLM_SYSTEM_PROMPT = """\
Extract the author(s) and publishing organization from the provided information.

You will receive:
- The URL of the page
- The first ~500 characters of the extracted article text
- Optionally, structured metadata extracted from the page's HTML meta tags

Return a JSON object with two fields:
- "person": the individual author(s) who wrote this content, comma-separated if \
multiple. null if not identifiable.
- "organization": the publishing entity (newspaper, university, company, website \
name). null if not identifiable.

Rules:
- "person" = individual human authors only (journalists, researchers, bloggers)
- "organization" = the publisher, institution, or platform (BBC, Nature, arXiv, \
Google Brain, Wikipedia)
- For collaborative platforms (Wikipedia, Reddit, Stack Overflow): person is null, \
organization is the platform name
- If genuinely unidentifiable, return null for that field

CRITICAL — Author names must be EXPLICITLY VISIBLE in the provided text or metadata. \
Do NOT infer, guess, or reconstruct author names from:
- URLs, DOIs, or citation references
- Partial initials or abbreviations you "recognise"
- Your training data or general knowledge about who wrote a paper
- The abstract or body text of an academic article (these rarely contain author names)

If the text you receive is an abstract, methodology section, or any content that \
does not explicitly state "by [name]" or "Author: [name]", return null for person. \
Academic paper abstracts almost never contain author names — do not hallucinate them.

When in doubt, return null. A missing author is far better than a wrong one.

Return ONLY the JSON object. No markdown fences."""

_LLM_USER_TEMPLATE = """\
URL: {url}

Content header:
{header}"""

_LLM_USER_TEMPLATE_WITH_META = """\
URL: {url}

Content header:
{header}

Page metadata (extracted from HTML meta tags — this is often reliable for \
author/publisher info but can sometimes be inaccurate or auto-generated; \
use it as supporting evidence alongside the content header and URL):
{metadata}"""


class LlmHeaderStrategy(AuthorStrategy):
    """Send first ~500 chars + URL to a cheap LLM for author extraction."""

    def __init__(self, gateway: ModelGateway) -> None:
        self._gateway = gateway

    async def extract(self, ctx: SourceContext) -> AuthorInfo | None:
        header = ctx.header_text.strip()
        if not header and not ctx.url:
            return None

        header_text = header[:500] if header else "(no content available)"

        # Use the metadata-enriched template when HTML metadata is available
        if ctx.html_metadata:
            meta_lines = "\n".join(f"  {k}: {v}" for k, v in ctx.html_metadata.items() if v)
            user_msg = _LLM_USER_TEMPLATE_WITH_META.format(
                url=ctx.url,
                header=header_text,
                metadata=meta_lines if meta_lines else "(empty)",
            )
        else:
            user_msg = _LLM_USER_TEMPLATE.format(
                url=ctx.url,
                header=header_text,
            )

        try:
            result = await self._gateway.generate_json(
                model_id=self._gateway.decomposition_model,
                messages=[{"role": "user", "content": user_msg}],
                system_prompt=_LLM_SYSTEM_PROMPT,
                temperature=0.0,
                max_tokens=200,
                reasoning_effort=self._gateway.decomposition_thinking_level or None,
            )
            if not result or not isinstance(result, dict):
                return None

            person = _clean_person_field(_clean_llm_field(result.get("person")))
            organization = _clean_llm_field(result.get("organization"))

            if not person and not organization:
                return None
            return AuthorInfo(person=person, organization=organization)

        except Exception:
            logger.warning("LLM author extraction failed for %s", ctx.url, exc_info=True)
            return None


def _clean_llm_field(value: object) -> str | None:
    """Clean an LLM-returned field value."""
    if value is None:
        return None
    s = str(value).strip()
    if s.lower() in ("null", "none", "n/a", "unknown", ""):
        return None
    return s


def _has_excessive_initials(name: str) -> bool:
    """Detect hallucinated academic-initial names like 'A. M. J. M. van der Heijden'.

    Returns True if the name has 4+ leading single-letter initials.
    3 initials is common in Dutch/European academic names (e.g.
    "G. J. P. van Breukelen", "J. R. R. Tolkien"), so the threshold
    is set at 4 to avoid false positives.
    """
    tokens = name.replace(".", " ").split()
    leading_initials = 0
    for token in tokens:
        if len(token) == 1 and token.isalpha():
            leading_initials += 1
        else:
            break
    return leading_initials >= 4


def _clean_person_field(person: str | None) -> str | None:
    """Filter individual author names from a comma-separated person string.

    Removes names that look like hallucinated academic initials and
    deduplicates repeated names.
    """
    if not person:
        return None

    seen: set[str] = set()
    cleaned: list[str] = []
    for name in person.split(","):
        name = name.strip()
        if not name:
            continue
        lower = name.lower()
        if lower in seen:
            continue  # drop duplicates
        if _has_excessive_initials(name):
            logger.debug("Dropping hallucinated-initials author name: '%s'", name)
            continue
        seen.add(lower)
        cleaned.append(name)

    return ", ".join(cleaned) if cleaned else None


# ── Chain runner ──────────────────────────────────────────────────


async def extract_author(
    strategies: list[AuthorStrategy],
    context: SourceContext,
) -> AuthorInfo:
    """Run strategies in order, return first successful result."""
    for strategy in strategies:
        try:
            result = await strategy.extract(context)
            if result and (result.person or result.organization):
                return result
        except Exception:
            logger.debug(
                "Author strategy %s failed for %s",
                type(strategy).__name__,
                context.url,
                exc_info=True,
            )
    return AuthorInfo()


def build_author_chain(
    gateway: ModelGateway,
    *,
    is_pdf: bool = False,
) -> list[AuthorStrategy]:
    """Build the default strategy chain for a source type."""
    if is_pdf:
        return [PdfMetadataStrategy(), LlmHeaderStrategy(gateway)]
    return [LlmHeaderStrategy(gateway)]
