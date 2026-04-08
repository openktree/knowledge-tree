"""Unit tests for the DOI shortcut provider."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from kt_providers.fetch.doi_provider import DoiContentFetcher, _format_metadata


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
