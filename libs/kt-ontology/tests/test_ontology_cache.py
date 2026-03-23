"""Tests for the CachedOntologyProvider Redis cache."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from kt_ontology.base import AncestorEntry, AncestryChain
from kt_ontology.cache import CachedOntologyProvider, _normalize_name

# ── Normalization tests ──────────────────────────────────────────


class TestNormalizeName:
    def test_basic(self) -> None:
        assert _normalize_name("Quicksort") == "quicksort"

    def test_spaces(self) -> None:
        assert _normalize_name("sorting algorithms") == "sorting_algorithms"

    def test_multiple_spaces(self) -> None:
        assert _normalize_name("  machine   learning  ") == "machine_learning"

    def test_mixed_case(self) -> None:
        assert _normalize_name("TCP Protocol") == "tcp_protocol"


# ── Cache tests ──────────────────────────────────────────────────


def _make_inner() -> MagicMock:
    """Create a mock OntologyProvider."""
    inner = MagicMock()
    inner.provider_id = "test_provider"
    inner.is_available = AsyncMock(return_value=True)
    inner.get_ancestry = AsyncMock(return_value=None)
    return inner


def _make_cached(inner: MagicMock, redis_mock: AsyncMock) -> CachedOntologyProvider:
    """Create a CachedOntologyProvider with mocked Redis."""
    cached = CachedOntologyProvider(inner=inner, redis_url="redis://localhost/0", ttl=3600)
    cached._redis = redis_mock
    return cached


@pytest.mark.asyncio
class TestCachedOntologyProvider:
    async def test_cache_miss_calls_inner(self) -> None:
        inner = _make_inner()
        chain = AncestryChain(
            ancestors=[AncestorEntry(name="algorithm")],
            source="test_provider",
        )
        inner.get_ancestry = AsyncMock(return_value=chain)

        redis_mock = AsyncMock()
        redis_mock.get = AsyncMock(return_value=None)  # Cache miss
        redis_mock.set = AsyncMock()

        cached = _make_cached(inner, redis_mock)
        result = await cached.get_ancestry("quicksort", "concept")

        assert result is not None
        assert result.ancestors[0].name == "algorithm"
        inner.get_ancestry.assert_awaited_once_with("quicksort", "concept")
        redis_mock.set.assert_awaited_once()

    async def test_cache_hit_skips_inner(self) -> None:
        inner = _make_inner()
        chain = AncestryChain(
            ancestors=[AncestorEntry(name="algorithm")],
            source="test_provider",
        )

        redis_mock = AsyncMock()
        redis_mock.get = AsyncMock(return_value=chain.model_dump_json().encode())

        cached = _make_cached(inner, redis_mock)
        result = await cached.get_ancestry("quicksort", "concept")

        assert result is not None
        assert result.ancestors[0].name == "algorithm"
        inner.get_ancestry.assert_not_awaited()

    async def test_cache_stores_negative_result(self) -> None:
        inner = _make_inner()
        inner.get_ancestry = AsyncMock(return_value=None)

        redis_mock = AsyncMock()
        redis_mock.get = AsyncMock(return_value=None)
        redis_mock.set = AsyncMock()

        cached = _make_cached(inner, redis_mock)
        result = await cached.get_ancestry("nonexistent", "concept")

        assert result is None
        # Should store null in cache
        redis_mock.set.assert_awaited_once()
        stored_value = redis_mock.set.call_args[0][1]
        assert json.loads(stored_value) is None

    async def test_cache_read_error_falls_through(self) -> None:
        """Redis read error should not prevent calling the inner provider."""
        inner = _make_inner()
        chain = AncestryChain(
            ancestors=[AncestorEntry(name="algorithm")],
            source="test_provider",
        )
        inner.get_ancestry = AsyncMock(return_value=chain)

        redis_mock = AsyncMock()
        redis_mock.get = AsyncMock(side_effect=ConnectionError("Redis down"))
        redis_mock.set = AsyncMock()

        cached = _make_cached(inner, redis_mock)
        result = await cached.get_ancestry("quicksort", "concept")

        assert result is not None
        inner.get_ancestry.assert_awaited_once()

    async def test_cache_key_format(self) -> None:
        inner = _make_inner()

        redis_mock = AsyncMock()
        redis_mock.get = AsyncMock(return_value=None)
        redis_mock.set = AsyncMock()

        cached = _make_cached(inner, redis_mock)
        await cached.get_ancestry("Machine Learning", "concept")

        expected_key = "ontology:test_provider:concept:machine_learning"
        redis_mock.get.assert_awaited_once_with(expected_key)

    async def test_is_available_delegates(self) -> None:
        inner = _make_inner()
        redis_mock = AsyncMock()
        cached = _make_cached(inner, redis_mock)

        result = await cached.is_available()
        assert result is True
        inner.is_available.assert_awaited_once()
