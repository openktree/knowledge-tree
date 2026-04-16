from unittest.mock import AsyncMock, patch

import httpx
import pytest

from kt_plugin_be_search_providers.providers.serper import SerperSearchProvider


@pytest.mark.asyncio
async def test_serper_search_success(respx_mock) -> None:
    url = SerperSearchProvider.SEARCH_URL
    respx_mock.post(url).mock(
        return_value=httpx.Response(
            200,
            json={
                "organic": [
                    {
                        "title": "Example Title",
                        "link": "https://example.com",
                        "snippet": "A short description.",
                        "position": 1,
                        "date": "2025-01-15",
                    }
                ]
            },
        )
    )

    provider = SerperSearchProvider(api_key="test-key")
    results = await provider.search("test query", max_results=5)

    assert len(results) == 1
    assert results[0].uri == "https://example.com"
    assert results[0].title == "Example Title"
    assert "Example Title" not in results[0].raw_content
    assert "A short description." in results[0].raw_content
    assert results[0].provider_id == "serper"
    assert results[0].provider_metadata == {"position": 1, "date": "2025-01-15"}


@pytest.mark.asyncio
async def test_serper_retries_on_429(respx_mock) -> None:
    url = SerperSearchProvider.SEARCH_URL
    respx_mock.post(url).mock(
        side_effect=[
            httpx.Response(429, text="rate limited"),
            httpx.Response(
                200,
                json={"organic": [{"title": "X", "link": "https://x.com", "snippet": "d"}]},
            ),
        ]
    )

    provider = SerperSearchProvider(api_key="test-key")

    with patch(
        "kt_plugin_be_search_providers.providers.serper.asyncio.sleep",
        new_callable=AsyncMock,
    ) as mock_sleep:
        results = await provider.search("test", max_results=1)

    assert len(results) == 1
    assert results[0].uri == "https://x.com"
    mock_sleep.assert_called_once_with(1.0)


@pytest.mark.asyncio
async def test_serper_raises_after_max_retries(respx_mock) -> None:
    url = SerperSearchProvider.SEARCH_URL
    respx_mock.post(url).mock(return_value=httpx.Response(429, text="rate limited"))

    provider = SerperSearchProvider(api_key="test-key")

    with (
        patch(
            "kt_plugin_be_search_providers.providers.serper.asyncio.sleep",
            new_callable=AsyncMock,
        ) as mock_sleep,
        pytest.raises(httpx.HTTPStatusError),
    ):
        await provider.search("test", max_results=1)

    assert mock_sleep.call_count == 3


@pytest.mark.asyncio
async def test_serper_no_retry_on_4xx(respx_mock) -> None:
    url = SerperSearchProvider.SEARCH_URL
    respx_mock.post(url).mock(return_value=httpx.Response(401, text="unauthorized"))

    provider = SerperSearchProvider(api_key="bad-key")

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await provider.search("test", max_results=1)

    assert exc_info.value.response.status_code == 401
