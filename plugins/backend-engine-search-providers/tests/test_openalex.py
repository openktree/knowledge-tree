from unittest.mock import AsyncMock, patch

import httpx
import pytest

from kt_plugin_be_search_providers.providers.openalex import (
    OpenAlexSearchProvider,
    reconstruct_abstract,
)


def _sample_work(
    *,
    doi: str | None = "https://doi.org/10.1234/abc",
    title: str = "Attention Is All You Need",
    inverted: dict[str, list[int]] | None = None,
) -> dict:
    return {
        "id": "https://openalex.org/W1",
        "doi": doi,
        "title": title,
        "publication_year": 2017,
        "cited_by_count": 42,
        "type": "article",
        "abstract_inverted_index": inverted or {"Attention": [0], "mechanism": [1], "works": [2]},
        "authorships": [
            {"author": {"display_name": "Ashish Vaswani"}},
            {"author": {"display_name": "Noam Shazeer"}},
        ],
        "open_access": {"is_oa": True, "oa_url": "https://arxiv.org/pdf/1706.03762"},
    }


def test_reconstruct_abstract_orders_by_position() -> None:
    inverted = {"world": [1], "hello": [0], "!": [2]}
    assert reconstruct_abstract(inverted) == "hello world !"


def test_reconstruct_abstract_handles_none() -> None:
    assert reconstruct_abstract(None) == ""
    assert reconstruct_abstract({}) == ""


@pytest.mark.asyncio
async def test_search_happy_path(respx_mock) -> None:
    respx_mock.get(OpenAlexSearchProvider.SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"results": [_sample_work()]})
    )

    provider = OpenAlexSearchProvider(mailto="charlie@example.com")
    results = await provider.search("attention transformers", max_results=5)

    assert len(results) == 1
    r = results[0]
    assert r.uri == "https://doi.org/10.1234/abc"
    assert r.title == "Attention Is All You Need"
    assert r.provider_id == "openalex"
    assert "Attention mechanism works" == r.raw_content
    assert r.provider_metadata is not None
    assert r.provider_metadata["publication_year"] == 2017
    assert r.provider_metadata["cited_by_count"] == 42
    assert r.provider_metadata["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    assert r.provider_metadata["open_access"]["is_oa"] is True


@pytest.mark.asyncio
async def test_search_falls_back_to_openalex_id_when_no_doi(respx_mock) -> None:
    respx_mock.get(OpenAlexSearchProvider.SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"results": [_sample_work(doi=None)]})
    )

    provider = OpenAlexSearchProvider()
    results = await provider.search("q")

    assert len(results) == 1
    assert results[0].uri == "https://openalex.org/W1"


@pytest.mark.asyncio
async def test_search_includes_mailto_param(respx_mock) -> None:
    route = respx_mock.get(OpenAlexSearchProvider.SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"results": []})
    )

    provider = OpenAlexSearchProvider(mailto="charlie@example.com")
    await provider.search("q", max_results=3)

    assert route.called
    sent_url = route.calls.last.request.url
    assert sent_url.params["mailto"] == "charlie@example.com"
    assert sent_url.params["search"] == "q"
    assert sent_url.params["per-page"] == "3"


@pytest.mark.asyncio
async def test_search_omits_mailto_when_unset(respx_mock) -> None:
    route = respx_mock.get(OpenAlexSearchProvider.SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"results": []})
    )

    provider = OpenAlexSearchProvider()
    await provider.search("q")

    assert route.called
    assert "mailto" not in route.calls.last.request.url.params


@pytest.mark.asyncio
async def test_search_retries_on_429(respx_mock) -> None:
    respx_mock.get(OpenAlexSearchProvider.SEARCH_URL).mock(
        side_effect=[
            httpx.Response(429, text="rate limited"),
            httpx.Response(200, json={"results": [_sample_work()]}),
        ]
    )

    provider = OpenAlexSearchProvider()

    with patch("kt_plugin_be_search_providers.providers.openalex.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        results = await provider.search("q")

    assert len(results) == 1
    mock_sleep.assert_called_once_with(1.0)


@pytest.mark.asyncio
async def test_search_raises_after_max_retries(respx_mock) -> None:
    respx_mock.get(OpenAlexSearchProvider.SEARCH_URL).mock(return_value=httpx.Response(503, text="unavailable"))

    provider = OpenAlexSearchProvider()

    with patch("kt_plugin_be_search_providers.providers.openalex.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(httpx.HTTPStatusError):
            await provider.search("q")


@pytest.mark.asyncio
async def test_search_no_retry_on_4xx(respx_mock) -> None:
    respx_mock.get(OpenAlexSearchProvider.SEARCH_URL).mock(return_value=httpx.Response(400, text="bad request"))

    provider = OpenAlexSearchProvider()
    with pytest.raises(httpx.HTTPStatusError):
        await provider.search("q")


@pytest.mark.asyncio
async def test_is_available_true_on_2xx(respx_mock) -> None:
    respx_mock.get(OpenAlexSearchProvider.SEARCH_URL).mock(return_value=httpx.Response(200, json={"results": []}))
    assert await OpenAlexSearchProvider().is_available() is True


@pytest.mark.asyncio
async def test_is_available_false_on_5xx(respx_mock) -> None:
    respx_mock.get(OpenAlexSearchProvider.SEARCH_URL).mock(return_value=httpx.Response(500, text="boom"))
    assert await OpenAlexSearchProvider().is_available() is False
