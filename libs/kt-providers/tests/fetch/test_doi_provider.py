"""Unit tests for the DOI-direct provider (doi.org URLs only)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from kt_providers.fetch.doi_enricher import DoiEnricher, format_metadata
from kt_providers.fetch.doi_provider import DoiContentFetcher
from kt_providers.fetch.types import FetchResult


@pytest.mark.asyncio
async def test_provider_id_and_always_available():
    p = DoiContentFetcher()
    assert p.provider_id == "doi"
    assert await p.is_available() is True


@pytest.mark.asyncio
async def test_non_doi_org_host_returns_immediately():
    p = DoiContentFetcher()
    result = await p.fetch("https://www.cell.com/some/article")
    assert result.success is False
    assert "doi.org" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_non_doi_org_host_example_com():
    p = DoiContentFetcher()
    result = await p.fetch("https://example.com/some/article")
    assert result.success is False
    assert "doi.org" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_doi_org_url_extracts_doi_from_path(monkeypatch: pytest.MonkeyPatch):
    p = DoiContentFetcher()

    crossref_message = {
        "DOI": "10.1234/abcd",
        "title": ["A great paper"],
        "author": [{"given": "Ada", "family": "Lovelace"}],
        "publisher": "Test Press",
        "container-title": ["Journal of Tests"],
        "issued": {"date-parts": [[2024, 5, 1]]},
        "abstract": "<jats:p>This is the abstract content.</jats:p>",
    }

    async def fake_fetch_crossref(self, doi):  # type: ignore[no-untyped-def]
        assert doi == "10.1234/abcd"
        return crossref_message

    async def fake_fetch_unpaywall(self, doi):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(DoiEnricher, "_fetch_crossref", fake_fetch_crossref)
    monkeypatch.setattr(DoiEnricher, "_fetch_unpaywall_oa", fake_fetch_unpaywall)

    result = await p.fetch("https://doi.org/10.1234/abcd")
    assert result.success is True
    assert "A great paper" in (result.content or "")
    assert "Ada Lovelace" in (result.content or "")
    assert "abstract content" in (result.content or "")


@pytest.mark.asyncio
async def test_dx_doi_org_also_handled(monkeypatch: pytest.MonkeyPatch):
    p = DoiContentFetcher()

    async def fake_fetch_crossref(self, doi):  # type: ignore[no-untyped-def]
        return {
            "DOI": doi,
            "title": ["A paper with a sufficiently long title for testing"],
            "publisher": "Publisher",
            "abstract": "This is a test abstract with enough content to pass the minimum length check.",
        }

    async def fake_fetch_unpaywall(self, doi):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(DoiEnricher, "_fetch_crossref", fake_fetch_crossref)
    monkeypatch.setattr(DoiEnricher, "_fetch_unpaywall_oa", fake_fetch_unpaywall)

    result = await p.fetch("https://dx.doi.org/10.9999/xyz")
    assert result.success is True


@pytest.mark.asyncio
async def test_empty_doi_path_returns_error():
    p = DoiContentFetcher()
    result = await p.fetch("https://doi.org/")
    assert result.success is False
    assert "empty" in (result.error or "").lower()


def test_format_metadata_strips_jats_xml():
    body = format_metadata(
        {
            "title": ["Hello"],
            "DOI": "10.1/x",
            "abstract": "<jats:p>plain <jats:italic>text</jats:italic></jats:p>",
        }
    )
    assert "Hello" in body
    assert "plain text" in body
    assert "<jats" not in body


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

    monkeypatch.setattr(DoiEnricher, "_fetch_crossref", fake_crossref)
    monkeypatch.setattr(DoiEnricher, "_fetch_unpaywall_oa", fake_unpaywall)
    monkeypatch.setattr(DoiEnricher, "_client_", fake_client)
    monkeypatch.setattr("kt_providers.fetch.doi_enricher.extract_pdf", fake_extract_pdf)

    result = await p.fetch("https://doi.org/10.1234/abcd")
    assert result.success is True
    assert result.content_type == "application/pdf"
    assert result.page_count == 12
    assert result.pdf_metadata == {"title": "A great paper"}
    assert "A great paper" in (result.content or "")
    assert "Full paper text extracted from PDF." in (result.content or "")
    assert "---" in (result.content or "")
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

    monkeypatch.setattr(DoiEnricher, "_fetch_crossref", fake_crossref)
    monkeypatch.setattr(DoiEnricher, "_fetch_unpaywall_oa", fake_unpaywall)
    monkeypatch.setattr(DoiEnricher, "_client_", fake_client)

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

    monkeypatch.setattr(DoiEnricher, "_fetch_crossref", fake_crossref)
    monkeypatch.setattr(DoiEnricher, "_fetch_unpaywall_oa", fake_unpaywall)
    monkeypatch.setattr(DoiEnricher, "_client_", fake_client)
    monkeypatch.setattr("kt_providers.fetch.doi_enricher.extract_pdf", fake_extract_pdf)

    result = await p.fetch("https://doi.org/10.1234/abcd")
    assert result.success is True
    assert result.content_type == "text/plain"
    assert "A great paper" in (result.content or "")
