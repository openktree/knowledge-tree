"""Unit tests for the DoiEnricher post-fetch enrichment hook."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from kt_providers.fetch.doi_enricher import DoiEnricher
from kt_providers.fetch.types import FetchResult


def _html_result(
    uri: str = "https://www.cell.com/article/123",
    doi: str | None = "10.1016/j.immuni.2006.07.008",
) -> FetchResult:
    """A successful FetchResult from curl_cffi with optional citation_doi."""
    meta = {"title": "Page Title"}
    if doi:
        meta["doi"] = doi
    return FetchResult(
        uri=uri,
        content="Full HTML content from the publisher. " * 20,
        content_type="text/html",
        html_metadata=meta,
        attempts=[],
    )


def _crossref_message() -> dict[str, object]:
    return {
        "DOI": "10.1016/j.immuni.2006.07.008",
        "title": ["Regulatory T cells in autoimmunity"],
        "author": [{"given": "Shimon", "family": "Sakaguchi"}],
        "publisher": "Elsevier",
        "container-title": ["Immunity"],
        "issued": {"date-parts": [[2006, 8]]},
        "abstract": "<jats:p>Abstract from Crossref.</jats:p>",
    }


# ── Enrichment skipping ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_skips_non_publisher_host():
    enricher = DoiEnricher()
    result = _html_result(uri="https://example.com/page", doi="10.1234/test")
    enriched = await enricher.enrich("https://example.com/page", result)
    assert enriched is result
    assert enriched.html_metadata.get("enriched_by") is None  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_skips_when_no_doi_in_metadata_or_url():
    enricher = DoiEnricher()
    result = _html_result(doi=None)
    # URL also has no DOI pattern
    enriched = await enricher.enrich("https://www.cell.com/article/S1074-7613(06)00309-8", result)
    assert enriched is result
    assert enriched.html_metadata.get("enriched_by") is None  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_skips_when_html_metadata_is_none():
    enricher = DoiEnricher()
    result = FetchResult(
        uri="https://www.cell.com/x",
        content="Hello world " * 20,
        html_metadata=None,
        attempts=[],
    )
    enriched = await enricher.enrich("https://www.cell.com/x", result)
    assert enriched is result


# ── Successful enrichment ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_enriches_with_crossref_metadata(monkeypatch: pytest.MonkeyPatch):
    enricher = DoiEnricher()

    async def fake_crossref(self, doi):  # type: ignore[no-untyped-def]
        return _crossref_message()

    async def fake_unpaywall(self, doi):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(DoiEnricher, "_fetch_crossref", fake_crossref)
    monkeypatch.setattr(DoiEnricher, "_fetch_unpaywall_oa", fake_unpaywall)

    result = _html_result()
    enriched = await enricher.enrich("https://www.cell.com/article/123", result)

    assert enriched.html_metadata is not None
    assert enriched.html_metadata["doi"] == "10.1016/j.immuni.2006.07.008"
    assert enriched.html_metadata["title"] == "Regulatory T cells in autoimmunity"
    assert enriched.html_metadata["publisher"] == "Elsevier"
    assert enriched.html_metadata["enriched_by"] == "crossref"
    assert enriched.html_metadata["oa_pdf_url"] is None
    # Content should be unchanged (no OA PDF)
    assert "Full HTML content" in (enriched.content or "")
    # Audit trail should include enricher attempt
    assert any(a.provider_id == "doi_enricher" and a.success for a in enriched.attempts)


@pytest.mark.asyncio
async def test_enriches_with_oa_pdf(monkeypatch: pytest.MonkeyPatch):
    enricher = DoiEnricher()

    async def fake_crossref(self, doi):  # type: ignore[no-untyped-def]
        return _crossref_message()

    async def fake_unpaywall(self, doi):  # type: ignore[no-untyped-def]
        return "https://europepmc.org/pdf/1234.pdf"

    pdf_response = MagicMock()
    pdf_response.status_code = 200
    pdf_response.headers = {"content-type": "application/pdf"}
    pdf_response.content = b"%PDF-bytes"

    client = MagicMock()
    client.get = AsyncMock(return_value=pdf_response)
    client.is_closed = False

    async def fake_client(self):  # type: ignore[no-untyped-def]
        return client

    def fake_extract_pdf(uri: str, pdf_bytes: bytes, ct: str) -> FetchResult:
        return FetchResult(
            uri=uri,
            content="Extracted PDF full text. " * 10,
            content_type="application/pdf",
            page_count=8,
            pdf_metadata={"title": "Paper"},
        )

    monkeypatch.setattr(DoiEnricher, "_fetch_crossref", fake_crossref)
    monkeypatch.setattr(DoiEnricher, "_fetch_unpaywall_oa", fake_unpaywall)
    monkeypatch.setattr(DoiEnricher, "_client_", fake_client)
    monkeypatch.setattr("kt_providers.fetch.doi_enricher.extract_pdf", fake_extract_pdf)

    result = _html_result()
    enriched = await enricher.enrich("https://www.cell.com/article/123", result)

    assert enriched.content_type == "application/pdf"
    assert enriched.page_count == 8
    assert "Extracted PDF full text." in (enriched.content or "")
    assert enriched.html_metadata is not None
    assert enriched.html_metadata["oa_pdf_url"] == "https://europepmc.org/pdf/1234.pdf"


@pytest.mark.asyncio
async def test_extracts_doi_from_url_when_not_in_metadata(monkeypatch: pytest.MonkeyPatch):
    """When html_metadata has no DOI but the URL contains one, enrichment still works."""
    enricher = DoiEnricher()

    async def fake_crossref(self, doi):  # type: ignore[no-untyped-def]
        return {"DOI": doi, "title": ["Found via URL"], "publisher": "Pub"}

    async def fake_unpaywall(self, doi):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(DoiEnricher, "_fetch_crossref", fake_crossref)
    monkeypatch.setattr(DoiEnricher, "_fetch_unpaywall_oa", fake_unpaywall)

    result = _html_result(
        uri="https://link.springer.com/article/10.1007/s00125-024-06301-2",
        doi=None,
    )
    enriched = await enricher.enrich("https://link.springer.com/article/10.1007/s00125-024-06301-2", result)
    assert enriched.html_metadata is not None
    assert enriched.html_metadata["doi"] == "10.1007/s00125-024-06301-2"
    assert enriched.html_metadata["enriched_by"] == "crossref"


# ── Failure handling ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_crossref_failure_returns_original(monkeypatch: pytest.MonkeyPatch):
    enricher = DoiEnricher()

    async def fake_crossref(self, doi):  # type: ignore[no-untyped-def]
        raise Exception("network error")

    monkeypatch.setattr(DoiEnricher, "_fetch_crossref", fake_crossref)

    result = _html_result()
    enriched = await enricher.enrich("https://www.cell.com/article/123", result)

    # Content unchanged
    assert "Full HTML content" in (enriched.content or "")
    # Failure recorded in attempts
    assert any(
        a.provider_id == "doi_enricher" and not a.success and "crossref" in (a.error or "") for a in enriched.attempts
    )


@pytest.mark.asyncio
async def test_crossref_404_returns_original(monkeypatch: pytest.MonkeyPatch):
    enricher = DoiEnricher()

    async def fake_crossref(self, doi):  # type: ignore[no-untyped-def]
        return None  # DOI not found in Crossref

    monkeypatch.setattr(DoiEnricher, "_fetch_crossref", fake_crossref)

    result = _html_result()
    enriched = await enricher.enrich("https://www.cell.com/article/123", result)

    assert "Full HTML content" in (enriched.content or "")
    assert any(a.provider_id == "doi_enricher" and not a.success for a in enriched.attempts)


# ── Unpaywall SSRF protection ────────────────────────────────────


@pytest.mark.asyncio
async def test_unpaywall_safe_url_passed_through(monkeypatch: pytest.MonkeyPatch):
    enricher = DoiEnricher()

    response = MagicMock()
    response.status_code = 200
    response.json = MagicMock(return_value={"best_oa_location": {"url_for_pdf": "https://arxiv.org/pdf/1234.pdf"}})
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    client.is_closed = False

    async def fake_client(self):  # type: ignore[no-untyped-def]
        return client

    async def fake_validate(uri: str) -> None:
        return None

    monkeypatch.setattr(DoiEnricher, "_client_", fake_client)
    monkeypatch.setattr("kt_providers.fetch.doi_enricher.validate_fetch_url", fake_validate)
    monkeypatch.setattr(
        "kt_providers.fetch.doi_enricher.get_settings",
        lambda: MagicMock(unpaywall_email="me@example.com", crossref_email=None, fetch_user_agent="ua"),
    )

    url = await enricher._fetch_unpaywall_oa("10.1234/abc")
    assert url == "https://arxiv.org/pdf/1234.pdf"


@pytest.mark.asyncio
async def test_unpaywall_poisoned_url_is_rejected(monkeypatch: pytest.MonkeyPatch):
    from kt_providers.fetch.url_safety import UnsafeUrlError

    enricher = DoiEnricher()

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

    monkeypatch.setattr(DoiEnricher, "_client_", fake_client)
    monkeypatch.setattr("kt_providers.fetch.doi_enricher.validate_fetch_url", fake_validate)
    monkeypatch.setattr(
        "kt_providers.fetch.doi_enricher.get_settings",
        lambda: MagicMock(unpaywall_email="me@example.com", crossref_email=None, fetch_user_agent="ua"),
    )

    url = await enricher._fetch_unpaywall_oa("10.1234/abc")
    assert url is None
