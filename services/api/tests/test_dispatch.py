"""Tests for the dispatch_with_graph helper."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_dispatch_with_graph_none_no_injection() -> None:
    """graph_id=None should NOT add graph_id to the input dict."""
    from kt_api.dispatch import dispatch_with_graph

    with patch("kt_hatchet.client.dispatch_workflow", new_callable=AsyncMock) as mock:
        mock.return_value = "run-123"
        result = await dispatch_with_graph("my_wf", {"topic": "ai"})

    assert result == "run-123"
    mock.assert_awaited_once_with("my_wf", {"topic": "ai"}, additional_metadata=None)


@pytest.mark.asyncio
async def test_dispatch_with_graph_injects_graph_id() -> None:
    """graph_id should be merged into the input dict."""
    from kt_api.dispatch import dispatch_with_graph

    with patch("kt_hatchet.client.dispatch_workflow", new_callable=AsyncMock) as mock:
        mock.return_value = "run-456"
        result = await dispatch_with_graph("my_wf", {"topic": "ai"}, graph_id="g-1")

    assert result == "run-456"
    call_args = mock.call_args
    assert call_args[0][1] == {"topic": "ai", "graph_id": "g-1"}


@pytest.mark.asyncio
async def test_dispatch_with_graph_does_not_mutate_original() -> None:
    """Original dict must not be modified."""
    from kt_api.dispatch import dispatch_with_graph

    original = {"topic": "ai"}
    with patch("kt_hatchet.client.dispatch_workflow", new_callable=AsyncMock) as mock:
        mock.return_value = "run-789"
        await dispatch_with_graph("my_wf", original, graph_id="g-2")

    assert "graph_id" not in original


@pytest.mark.asyncio
async def test_dispatch_with_graph_passes_metadata() -> None:
    """additional_metadata should be forwarded."""
    from kt_api.dispatch import dispatch_with_graph

    meta = {"conversation_id": "c-1"}
    with patch("kt_hatchet.client.dispatch_workflow", new_callable=AsyncMock) as mock:
        mock.return_value = "run-aaa"
        await dispatch_with_graph("my_wf", {"x": 1}, additional_metadata=meta)

    mock.assert_awaited_once_with("my_wf", {"x": 1}, additional_metadata=meta)
