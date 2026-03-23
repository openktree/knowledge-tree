"""Tests for the Qdrant client module."""

from unittest.mock import patch

import kt_qdrant.client as client_module
from kt_qdrant.client import close_qdrant_client, get_qdrant_client


class TestGetQdrantClient:
    def setup_method(self) -> None:
        # Reset singleton between tests
        client_module._client = None

    def teardown_method(self) -> None:
        client_module._client = None

    def test_creates_client(self) -> None:
        with patch("kt_qdrant.client.get_settings") as mock_settings:
            mock_settings.return_value.qdrant_url = "http://localhost:6333"
            client = get_qdrant_client()
            assert client is not None

    def test_returns_singleton(self) -> None:
        with patch("kt_qdrant.client.get_settings") as mock_settings:
            mock_settings.return_value.qdrant_url = "http://localhost:6333"
            client1 = get_qdrant_client()
            client2 = get_qdrant_client()
            assert client1 is client2

    async def test_close_client(self) -> None:
        with patch("kt_qdrant.client.get_settings") as mock_settings:
            mock_settings.return_value.qdrant_url = "http://localhost:6333"
            get_qdrant_client()
            assert client_module._client is not None
            await close_qdrant_client()
            assert client_module._client is None

    async def test_close_when_none(self) -> None:
        # Should not raise
        await close_qdrant_client()
