from unittest.mock import AsyncMock, patch

import httpx
import pytest

from kt_plugin_be_search_providers.providers.brave import BraveSearchProvider


@pytest.mark.asyncio
async def test_brave_retries_on_429(respx_mock) -> None:
    url = BraveSearchProvider.SEARCH_URL
    respx_mock.get(url).mock(
        side_effect=[
            httpx.Response(429, text="rate limited"),
            httpx.Response(
                200,
                json={"web": {"results": [{"url": "https://x.com", "title": "X", "description": "d"}]}},
            ),
        ]
    )

    provider = BraveSearchProvider(api_key="test-key")

    with patch(
        "kt_plugin_be_search_providers.providers.brave.asyncio.sleep",
        new_callable=AsyncMock,
    ) as mock_sleep:
        results = await provider.search("test", max_results=1)

    assert len(results) == 1
    assert results[0].uri == "https://x.com"
    mock_sleep.assert_called_once_with(1.0)


@pytest.mark.asyncio
async def test_brave_raises_after_max_retries(respx_mock) -> None:
    url = BraveSearchProvider.SEARCH_URL
    respx_mock.get(url).mock(return_value=httpx.Response(429, text="rate limited"))

    provider = BraveSearchProvider(api_key="test-key")

    with (
        patch(
            "kt_plugin_be_search_providers.providers.brave.asyncio.sleep",
            new_callable=AsyncMock,
        ) as mock_sleep,
        pytest.raises(httpx.HTTPStatusError),
    ):
        await provider.search("test", max_results=1)

    assert mock_sleep.call_count == 3


@pytest.mark.asyncio
async def test_brave_no_retry_on_4xx(respx_mock) -> None:
    url = BraveSearchProvider.SEARCH_URL
    respx_mock.get(url).mock(return_value=httpx.Response(401, text="unauthorized"))

    provider = BraveSearchProvider(api_key="bad-key")

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await provider.search("test", max_results=1)

    assert exc_info.value.response.status_code == 401
