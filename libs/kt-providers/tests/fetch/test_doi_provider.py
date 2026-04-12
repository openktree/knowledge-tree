"""Unit tests for the DOI shortcut provider."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from kt_providers.fetch.doi_provider import DoiContentFetcher, _format_metadata
from kt_providers.fetch.types import FetchResult


@pytest.mark.asyncio
async def test_provider_id_and_always_available():
    p = DoiContentFetcher()
    assert p.provider_id == "doi"
    assert await p.is_available() is True


@pytest.mark.asyncio
async def test_non_publisher_host_returns_immediately():
    p = DoiContentFetcher()
    result = await p.fetch("https://example.com/some/article")
    assert result.success is False
    assert "publisher" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_doi_org_url_extracts_doi_from_path(monkeypatch: pytest.MonkeyPatch):
    p = DoiContentFetcher()

    crossref_payload = {
        "message": {
            "DOI": "10.1234/abcd",
            "title": ["A great paper"],
            "author": [{"given": "Ada", "family": "Lovelace"}],
            "publisher": "Test Press",
            "container-title": ["Journal of Tests"],
            "issued": {"date-parts": [[2024, 5, 1]]},
            "abstract": "<jats:p>This is the abstract content.</jats:p>",
        }
    }

    async def fake_fetch_crossref(self, doi):  # type: ignore[no-untyped-def]
        assert doi == "10.1234/abcd"
        return crossref_payload["message"]

    async def fake_fetch_unpaywall(self, doi):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(DoiContentFetcher, "_fetch_crossref", fake_fetch_crossref)
    monkeypatch.setattr(DoiContentFetcher, "_fetch_unpaywall_oa", fake_fetch_unpaywall)

    result = await p.fetch("https://doi.org/10.1234/abcd")
    assert result.success is True
    assert result.provider_id is None  # registry sets this, not the provider
    assert "A great paper" in (result.content or "")
    assert "Ada Lovelace" in (result.content or "")
    assert "abstract content" in (result.content or "")


def test_format_metadata_strips_jats_xml():
    body = _format_metadata(
        {
            "title": ["Hello"],
            "DOI": "10.1/x",
            "abstract": "<jats:p>plain <jats:italic>text</jats:italic></jats:p>",
        }
    )
    assert "Hello" in body
    assert "plain text" in body
    assert "<jats" not in body


@pytest.mark.asyncio
async def test_unpaywall_url_passed_through_when_safe(monkeypatch: pytest.MonkeyPatch):
    """A normal public Unpaywall URL is returned unchanged."""
    p = DoiContentFetcher()

    response = MagicMock()
    response.status_code = 200
    response.json = MagicMock(return_value={"best_oa_location": {"url_for_pdf": "https://arxiv.org/pdf/1234.pdf"}})
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    client.is_closed = False

    async def fake_client(self):  # type: ignore[no-untyped-def]
        return client

    async def fake_validate(uri: str) -> None:
        # Pretend the URL safety check passed.
        return None

    monkeypatch.setattr(DoiContentFetcher, "_client_", fake_client)
    monkeypatch.setattr("kt_providers.fetch.doi_provider.validate_fetch_url", fake_validate)
    monkeypatch.setattr(
        "kt_providers.fetch.doi_provider.get_settings",
        lambda: MagicMock(unpaywall_email="me@example.com", crossref_email=None, fetch_user_agent="ua"),
    )

    url = await p._fetch_unpaywall_oa("10.1234/abc")
    assert url == "https://arxiv.org/pdf/1234.pdf"


@pytest.mark.asyncio
async def test_unpaywall_poisoned_url_is_rejected(monkeypatch: pytest.MonkeyPatch):
    """A poisoned Unpaywall response that returns a private/loopback URL
    must be dropped — Unpaywall is third-party JSON and we cannot trust
    its `url_for_pdf` to be safe to fetch."""
    from kt_providers.fetch.url_safety import UnsafeUrlError

    p = DoiContentFetcher()

    response = MagicMock()
    response.status_code = 200
    response.json = MagicMock(return_value={"best_oa_location": {"url_for_pdf": "http://169.254.169.254/admin"}})
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    client.is_closed = False

    async def fake_client(self):  # type: ignore[no-untyped-def]
        return client

    async def fake_validate(uri: str) -> None:
        if "169.254" in uri:
            raise UnsafeUrlError("metadata endpoint")
        return None

    monkeypatch.setattr(DoiContentFetcher, "_client_", fake_client)
    monkeypatch.setattr("kt_providers.fetch.doi_provider.validate_fetch_url", fake_validate)
    monkeypatch.setattr(
        "kt_providers.fetch.doi_provider.get_settings",
        lambda: MagicMock(unpaywall_email="me@example.com", crossref_email=None, fetch_user_agent="ua"),
    )

    url = await p._fetch_unpaywall_oa("10.1234/abc")
    assert url is None  # poisoned URL silently dropped


@pytest.mark.asyncio
async def test_extract_doi_from_meta_tag(monkeypatch: pytest.MonkeyPatch):
    """When the DOI isn't in the URL, fall back to parsing citation_doi meta."""
    p = DoiContentFetcher()

    response = MagicMock()
    response.status_code = 200
    response.text = '<html><head><meta name="citation_doi" content="10.5555/found-doi"/></head></html>'

    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    client.is_closed = False

    async def fake_client(self):  # type: ignore[no-untyped-def]
        return client

    monkeypatch.setattr(DoiContentFetcher, "_client_", fake_client)

    doi = await p._extract_doi("https://www.cell.com/some/page")
    assert doi == "10.5555/found-doi"


# ── OA PDF download & extraction tests ──────────────────────────


def _crossref_message() -> dict[str, object]:
    return {
        "DOI": "10.1234/abcd",
        "title": ["A great paper"],
        "author": [{"given": "Ada", "family": "Lovelace"}],
        "publisher": "Test Press",
        "abstract": "<jats:p>Abstract text.</jats:p>",
    }


@pytest.mark.asyncio
async def test_doi_fetcher_downloads_oa_pdf(monkeypatch: pytest.MonkeyPatch):
    """When Unpaywall provides an OA URL and the PDF downloads successfully,
    the result should contain both metadata and extracted PDF text."""
    p = DoiContentFetcher()

    async def fake_crossref(self, doi):  # type: ignore[no-untyped-def]
        return _crossref_message()

    async def fake_unpaywall(self, doi):  # type: ignore[no-untyped-def]
        return "https://arxiv.org/pdf/1234.pdf"

    pdf_response = MagicMock()
    pdf_response.status_code = 200
    pdf_response.headers = {"content-type": "application/pdf"}
    pdf_response.content = b"%PDF-fake-bytes"

    client = MagicMock()
    client.get = AsyncMock(return_value=pdf_response)
    client.is_closed = False

    async def fake_client(self):  # type: ignore[no-untyped-def]
        return client

    def fake_extract_pdf(uri: str, pdf_bytes: bytes, ct: str) -> FetchResult:
        return FetchResult(
            uri=uri,
            content="Full paper text extracted from PDF. " * 10,
            content_type="application/pdf",
            page_count=12,
            pdf_metadata={"title": "A great paper"},
        )

    monkeypatch.setattr(DoiContentFetcher, "_fetch_crossref", fake_crossref)
    monkeypatch.setattr(DoiContentFetcher, "_fetch_unpaywall_oa", fake_unpaywall)
    monkeypatch.setattr(DoiContentFetcher, "_client_", fake_client)
    monkeypatch.setattr("kt_providers.fetch.doi_provider.extract_pdf", fake_extract_pdf)

    result = await p.fetch("https://doi.org/10.1234/abcd")
    assert result.success is True
    assert result.content_type == "application/pdf"
    assert result.page_count == 12
    assert result.pdf_metadata == {"title": "A great paper"}
    # Should contain both metadata header and PDF text
    assert "A great paper" in (result.content or "")
    assert "Full paper text extracted from PDF." in (result.content or "")
    assert "---" in (result.content or "")
    # html_metadata should still have DOI and OA URL
    assert result.html_metadata is not None
    assert result.html_metadata["doi"] == "10.1234/abcd"
    assert result.html_metadata["oa_pdf_url"] == "https://arxiv.org/pdf/1234.pdf"


@pytest.mark.asyncio
async def test_doi_fetcher_falls_back_on_pdf_download_failure(monkeypatch: pytest.MonkeyPatch):
    """When OA PDF download fails, fall back to metadata-only result."""
    p = DoiContentFetcher()

    async def fake_crossref(self, doi):  # type: ignore[no-untyped-def]
        return _crossref_message()

    async def fake_unpaywall(self, doi):  # type: ignore[no-untyped-def]
        return "https://arxiv.org/pdf/1234.pdf"

    client = MagicMock()
    client.get = AsyncMock(side_effect=Exception("connection refused"))
    client.is_closed = False

    async def fake_client(self):  # type: ignore[no-untyped-def]
        return client

    monkeypatch.setattr(DoiContentFetcher, "_fetch_crossref", fake_crossref)
    monkeypatch.setattr(DoiContentFetcher, "_fetch_unpaywall_oa", fake_unpaywall)
    monkeypatch.setattr(DoiContentFetcher, "_client_", fake_client)

    result = await p.fetch("https://doi.org/10.1234/abcd")
    assert result.success is True
    assert result.content_type == "text/plain"
    assert "A great paper" in (result.content or "")
    assert result.html_metadata is not None
    assert result.html_metadata["oa_pdf_url"] == "https://arxiv.org/pdf/1234.pdf"


@pytest.mark.asyncio
async def test_doi_fetcher_falls_back_when_pdf_extraction_fails(monkeypatch: pytest.MonkeyPatch):
    """When PDF downloads but extraction fails, fall back to metadata-only."""
    p = DoiContentFetcher()

    async def fake_crossref(self, doi):  # type: ignore[no-untyped-def]
        return _crossref_message()

    async def fake_unpaywall(self, doi):  # type: ignore[no-untyped-def]
        return "https://arxiv.org/pdf/1234.pdf"

    pdf_response = MagicMock()
    pdf_response.status_code = 200
    pdf_response.headers = {"content-type": "application/pdf"}
    pdf_response.content = b"not-a-real-pdf"

    client = MagicMock()
    client.get = AsyncMock(return_value=pdf_response)
    client.is_closed = False

    async def fake_client(self):  # type: ignore[no-untyped-def]
        return client

    def fake_extract_pdf(uri: str, pdf_bytes: bytes, ct: str) -> FetchResult:
        return FetchResult(uri=uri, error="corrupt PDF")

    monkeypatch.setattr(DoiContentFetcher, "_fetch_crossref", fake_crossref)
    monkeypatch.setattr(DoiContentFetcher, "_fetch_unpaywall_oa", fake_unpaywall)
    monkeypatch.setattr(DoiContentFetcher, "_client_", fake_client)
    monkeypatch.setattr("kt_providers.fetch.doi_provider.extract_pdf", fake_extract_pdf)

    result = await p.fetch("https://doi.org/10.1234/abcd")
    assert result.success is True
    assert result.content_type == "text/plain"
    assert "A great paper" in (result.content or "")
