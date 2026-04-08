"""DOI-aware shortcut provider for academic publishers.

Many academic publisher domains (cell.com, sciencedirect.com, nature.com,
springer.com, wiley.com, …) sit behind aggressive WAFs that block plain HTTP
clients **and** also expose canonical machine-readable identifiers (DOIs)
plus open metadata APIs that don't bot-block at all.

This provider:
1. Recognises a publisher URL.
2. Extracts the DOI either from the URL path or by fetching the landing page
   and parsing `<meta name="citation_doi">`.
3. Queries Crossref for canonical metadata (title, authors, abstract, …).
4. Optionally queries Unpaywall for an open-access PDF link and follows it.

For an Elsevier article like the one in the original bug report
(`https://www.cell.com/molecular-cell/fulltext/S1097-2765(23)00605-6`) this
sidesteps the entire scraping problem — the abstract + structured metadata
come from Crossref, and if an OA PDF exists Unpaywall hands us a direct URL
that the rest of the chain can fetch unblocked.

Both Crossref and Unpaywall are free public APIs but ask for a contact
email in the User-Agent (Crossref: "polite pool"; Unpaywall: required).
Wire those via `crossref_email` / `unpaywall_email` settings.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

import httpx

from kt_config.settings import get_settings
from kt_providers.fetch.base import ContentFetcherProvider
from kt_providers.fetch.canonical import DOI_REGEX
from kt_providers.fetch.types import MIN_EXTRACTED_LENGTH, FetchResult
from kt_providers.fetch.url_safety import UnsafeUrlError, validate_fetch_url

logger = logging.getLogger(__name__)

# Hosts where the DOI shortcut is known to be useful.  Anything else falls
# through immediately so we don't waste a Crossref roundtrip on, say, a
# random blog post.
PUBLISHER_HOSTS: frozenset[str] = frozenset(
    {
        "cell.com",
        "www.cell.com",
        "sciencedirect.com",
        "www.sciencedirect.com",
        "linkinghub.elsevier.com",
        "nature.com",
        "www.nature.com",
        "link.springer.com",
        "springer.com",
        "onlinelibrary.wiley.com",
        "wiley.com",
        "tandfonline.com",
        "www.tandfonline.com",
        "journals.sagepub.com",
        "academic.oup.com",
        "ieeexplore.ieee.org",
        "dl.acm.org",
        "jstor.org",
        "www.jstor.org",
        "pubmed.ncbi.nlm.nih.gov",
        "www.ncbi.nlm.nih.gov",
        "pmc.ncbi.nlm.nih.gov",
        "biorxiv.org",
        "www.biorxiv.org",
        "medrxiv.org",
        "www.medrxiv.org",
        "doi.org",
        "dx.doi.org",
    }
)

# DOI regex is shared with the canonicalization helpers in
# ``kt_providers.fetch.canonical`` so the pattern can never drift.  The
# meta-tag pattern is local since only this provider's landing-page
# fallback uses it (the cross-fetcher path goes through
# ``extract_html_metadata`` which scrapes the same tag separately).
_DOI_RE = DOI_REGEX
_DOI_META_RE = re.compile(
    r"<meta[^>]+name=[\"']citation_doi[\"'][^>]+content=[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)

CROSSREF_API = "https://api.crossref.org/works/{doi}"
UNPAYWALL_API = "https://api.unpaywall.org/v2/{doi}"


class DoiContentFetcher(ContentFetcherProvider):
    """Fetcher that resolves academic URLs via Crossref/Unpaywall."""

    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    @property
    def provider_id(self) -> str:
        return "doi"

    async def is_available(self) -> bool:
        # Always available — uses public APIs.  Individual fetches no-op
        # for non-publisher hosts.
        return True

    async def _client_(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            ua = get_settings().fetch_user_agent
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                follow_redirects=True,
                headers={"User-Agent": ua, "Accept": "application/json"},
            )
        return self._client

    async def fetch(self, uri: str) -> FetchResult:
        host = urlparse(uri).netloc.lower()
        if host not in PUBLISHER_HOSTS:
            # Not an academic URL — let a downstream provider handle it.
            return FetchResult(uri=uri, error="not a known publisher host")

        doi = await self._extract_doi(uri)
        if doi is None:
            return FetchResult(uri=uri, error="no DOI found for URL")

        try:
            metadata = await self._fetch_crossref(doi)
        except Exception as e:
            logger.debug("Crossref fetch failed for %s: %s", doi, e)
            return FetchResult(uri=uri, error=f"crossref error: {e}")

        if metadata is None:
            return FetchResult(uri=uri, error=f"DOI {doi} not in Crossref")

        body = _format_metadata(metadata)
        if not body or len(body) < MIN_EXTRACTED_LENGTH:
            return FetchResult(
                uri=uri,
                error="Crossref metadata too sparse",
                content_type="application/json",
            )

        # Best-effort: try to enrich with an Unpaywall OA PDF link.
        oa_url = None
        try:
            oa_url = await self._fetch_unpaywall_oa(doi)
        except Exception:
            logger.debug("Unpaywall lookup failed for %s", doi, exc_info=True)

        if oa_url:
            body = f"{body}\n\nOpen-access PDF: {oa_url}"

        meta_out: dict[str, str | None] = {"doi": doi, "oa_pdf_url": oa_url}
        title_value = metadata.get("title")
        if isinstance(title_value, list) and title_value:
            meta_out["title"] = str(title_value[0])
        elif isinstance(title_value, str):
            meta_out["title"] = title_value
        publisher = metadata.get("publisher")
        if isinstance(publisher, str):
            meta_out["publisher"] = publisher

        return FetchResult(
            uri=uri,
            content=body,
            content_type="text/plain",
            html_metadata=meta_out,
        )

    async def _extract_doi(self, uri: str) -> str | None:
        # 1. doi.org/<doi> → trivial extract from the path.
        parsed = urlparse(uri)
        if parsed.netloc.lower() in ("doi.org", "dx.doi.org"):
            return parsed.path.lstrip("/") or None

        # 2. Try to find a DOI in the URL itself (some publisher URLs include it).
        m = _DOI_RE.search(uri)
        if m:
            return m.group(1).rstrip(".)")

        # 3. Fall back to fetching the landing page and grepping the meta tag.
        try:
            client = await self._client_()
            response = await client.get(uri)
        except Exception as e:
            logger.debug("DOI landing-page fetch failed for %s: %s", uri, e)
            return None

        if response.status_code >= 400:
            return None

        body = response.text or ""
        m = _DOI_META_RE.search(body)
        if m:
            return m.group(1).strip()

        m = _DOI_RE.search(body)
        if m:
            return m.group(1).rstrip(".)")
        return None

    async def _fetch_crossref(self, doi: str) -> dict[str, object] | None:
        client = await self._client_()
        settings = get_settings()
        headers = {}
        email = getattr(settings, "crossref_email", None)
        if email:
            headers["User-Agent"] = f"{settings.fetch_user_agent} (mailto:{email})"
        response = await client.get(CROSSREF_API.format(doi=doi), headers=headers or None)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        message = data.get("message")
        return message if isinstance(message, dict) else None

    async def _fetch_unpaywall_oa(self, doi: str) -> str | None:
        settings = get_settings()
        email = getattr(settings, "unpaywall_email", None) or getattr(settings, "crossref_email", None)
        if not email:
            # Unpaywall requires an email; skip silently if not configured.
            return None
        client = await self._client_()
        response = await client.get(UNPAYWALL_API.format(doi=doi), params={"email": email})
        if response.status_code != 200:
            return None
        data = response.json()
        best = data.get("best_oa_location") or {}
        url = best.get("url_for_pdf") or best.get("url")
        if not url:
            return None
        url_str = str(url)
        # Defense-in-depth: Unpaywall is third-party JSON.  We hand the
        # resulting URL back to the registry / pipeline, and a future
        # consumer might fetch it without re-checking.  Run it through
        # the same SSRF guard so a poisoned Unpaywall response cannot
        # smuggle a private/loopback URL into our system.
        try:
            await validate_fetch_url(url_str)
        except UnsafeUrlError as e:
            logger.warning(
                "rejecting unsafe Unpaywall PDF url for DOI %s: %s (%s)",
                doi,
                url_str,
                e,
            )
            return None
        return url_str

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


def _format_metadata(message: dict[str, object]) -> str:
    """Render a Crossref `message` payload as a plain-text body."""
    parts: list[str] = []

    title = message.get("title")
    if isinstance(title, list) and title:
        parts.append(f"Title: {title[0]}")
    elif isinstance(title, str):
        parts.append(f"Title: {title}")

    authors = message.get("author")
    if isinstance(authors, list) and authors:
        names = [
            " ".join(filter(None, [a.get("given"), a.get("family")]))  # type: ignore[union-attr]
            for a in authors
            if isinstance(a, dict)
        ]
        names = [n for n in names if n]
        if names:
            parts.append(f"Authors: {', '.join(names)}")

    publisher = message.get("publisher")
    if publisher:
        parts.append(f"Publisher: {publisher}")

    container = message.get("container-title")
    if isinstance(container, list) and container:
        parts.append(f"Journal: {container[0]}")

    issued = message.get("issued") or {}
    date_parts = (issued.get("date-parts") or [[]])[0] if isinstance(issued, dict) else []
    if date_parts:
        parts.append(f"Published: {'-'.join(str(p) for p in date_parts)}")

    abstract = message.get("abstract")
    if isinstance(abstract, str) and abstract:
        # Crossref abstracts are stored as JATS XML; strip tags crudely.
        clean = re.sub(r"<[^>]+>", "", abstract).strip()
        if clean:
            parts.append(f"\nAbstract:\n{clean}")

    doi = message.get("DOI")
    if doi:
        parts.append(f"\nDOI: {doi}")

    return "\n".join(parts)
