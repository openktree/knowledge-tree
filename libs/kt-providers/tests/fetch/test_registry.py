"""Unit tests for FetchProviderRegistry: chain order, preferred resolution, host learning."""

from __future__ import annotations

import pytest

from kt_providers.fetch.base import ContentFetcherProvider
from kt_providers.fetch.host_pref import InMemoryHostPreferenceStore
from kt_providers.fetch.registry import FetchProviderRegistry
from kt_providers.fetch.types import FetchResult


class FakeProvider(ContentFetcherProvider):
    """Test double whose `fetch` is fully scripted."""

    def __init__(
        self,
        provider_id: str,
        *,
        return_value: FetchResult | None = None,
        available: bool = True,
        raises: BaseException | None = None,
    ) -> None:
        self._pid = provider_id
        self._return_value = return_value
        self._available = available
        self._raises = raises
        self.calls: list[str] = []

    @property
    def provider_id(self) -> str:
        return self._pid

    async def is_available(self) -> bool:
        return self._available

    async def fetch(self, uri: str) -> FetchResult:
        self.calls.append(uri)
        if self._raises is not None:
            raise self._raises
        assert self._return_value is not None
        return self._return_value


def _success(provider_id: str) -> FetchResult:
    return FetchResult(uri="https://example.com", content="x" * 200, provider_id=None)


def _failure(provider_id: str, error: str = "boom") -> FetchResult:
    return FetchResult(uri="https://example.com", error=error)


@pytest.mark.asyncio
async def test_chain_returns_first_successful_provider():
    p1 = FakeProvider("a", return_value=_failure("a"))
    p2 = FakeProvider("b", return_value=_success("b"))
    p3 = FakeProvider("c", return_value=_success("c"))

    reg = FetchProviderRegistry([p1, p2, p3], chain=["a", "b", "c"])
    result = await reg.fetch("https://example.com")

    assert result.success is True
    assert result.provider_id == "b"
    assert [a.provider_id for a in result.attempts] == ["a", "b"]
    assert p3.calls == []  # short-circuited


@pytest.mark.asyncio
async def test_unavailable_provider_is_skipped_with_attempt_logged():
    p1 = FakeProvider("a", available=False)
    p2 = FakeProvider("b", return_value=_success("b"))

    reg = FetchProviderRegistry([p1, p2], chain=["a", "b"])
    result = await reg.fetch("https://example.com")

    assert result.success is True
    assert result.provider_id == "b"
    attempts = result.attempts
    assert attempts[0].provider_id == "a"
    assert attempts[0].success is False
    assert attempts[0].error == "unavailable"


@pytest.mark.asyncio
async def test_provider_exception_is_caught_and_chain_continues():
    p1 = FakeProvider("a", raises=RuntimeError("kaboom"))
    p2 = FakeProvider("b", return_value=_success("b"))

    reg = FetchProviderRegistry([p1, p2], chain=["a", "b"])
    result = await reg.fetch("https://example.com")

    assert result.success is True
    assert result.provider_id == "b"
    assert "kaboom" in (result.attempts[0].error or "")


@pytest.mark.asyncio
async def test_all_fetchers_failed_returns_audit_trail():
    p1 = FakeProvider("a", return_value=_failure("a", "fail-a"))
    p2 = FakeProvider("b", return_value=_failure("b", "fail-b"))

    reg = FetchProviderRegistry([p1, p2], chain=["a", "b"])
    result = await reg.fetch("https://example.com")

    assert result.success is False
    assert result.error in {"fail-b", "all fetchers failed"}
    assert [a.provider_id for a in result.attempts] == ["a", "b"]
    assert all(not a.success for a in result.attempts)


@pytest.mark.asyncio
async def test_explicit_preferred_is_tried_first():
    p1 = FakeProvider("a", return_value=_success("a"))
    p2 = FakeProvider("b", return_value=_success("b"))

    reg = FetchProviderRegistry([p1, p2], chain=["a", "b"])
    result = await reg.fetch("https://example.com", preferred="b")

    assert result.provider_id == "b"
    assert p1.calls == []  # never tried


@pytest.mark.asyncio
async def test_explicit_preferred_falls_back_on_failure_and_skips_duplicate():
    p1 = FakeProvider("a", return_value=_success("a"))
    p2 = FakeProvider("b", return_value=_failure("b"))

    reg = FetchProviderRegistry([p1, p2], chain=["a", "b"])
    result = await reg.fetch("https://example.com", preferred="b")

    assert result.provider_id == "a"
    # b should only have been tried once (as preferred), not again from the chain
    assert len(p2.calls) == 1
    assert [a.provider_id for a in result.attempts] == ["b", "a"]


@pytest.mark.asyncio
async def test_host_overrides_resolve_to_preferred():
    p1 = FakeProvider("httpx", return_value=_success("httpx"))
    p2 = FakeProvider("flaresolverr", return_value=_success("flaresolverr"))

    reg = FetchProviderRegistry(
        [p1, p2],
        chain=["httpx", "flaresolverr"],
        host_overrides={"cell.com": "flaresolverr"},
    )
    result = await reg.fetch("https://www.cell.com/some/article")

    # cell.com (suffix-matched against the override) should bypass the chain
    assert result.provider_id == "flaresolverr"
    assert p1.calls == []


@pytest.mark.asyncio
async def test_explicit_preferred_overrides_static_host_override():
    p1 = FakeProvider("httpx", return_value=_success("httpx"))
    p2 = FakeProvider("flaresolverr", return_value=_success("flaresolverr"))

    reg = FetchProviderRegistry(
        [p1, p2],
        chain=["httpx", "flaresolverr"],
        host_overrides={"cell.com": "flaresolverr"},
    )
    result = await reg.fetch("https://www.cell.com/x", preferred="httpx")
    assert result.provider_id == "httpx"


@pytest.mark.asyncio
async def test_learned_host_preference_recorded_on_non_default_winner():
    p1 = FakeProvider("a", return_value=_failure("a"))
    p2 = FakeProvider("b", return_value=_success("b"))

    store = InMemoryHostPreferenceStore()
    reg = FetchProviderRegistry([p1, p2], chain=["a", "b"], host_pref_store=store)
    await reg.fetch("https://example.com/path")

    # `b` is the second tier and produced the success — record it
    assert await store.get("example.com") == "b"


@pytest.mark.asyncio
async def test_learned_host_preference_used_first_next_call():
    p1 = FakeProvider("a", return_value=_success("a"))
    p2 = FakeProvider("b", return_value=_success("b"))

    store = InMemoryHostPreferenceStore()
    await store.record("example.com", "b")

    reg = FetchProviderRegistry([p1, p2], chain=["a", "b"], host_pref_store=store)
    result = await reg.fetch("https://example.com/x")

    assert result.provider_id == "b"
    assert p1.calls == []


@pytest.mark.asyncio
async def test_default_winner_is_not_recorded_to_avoid_noise():
    p1 = FakeProvider("a", return_value=_success("a"))
    p2 = FakeProvider("b", return_value=_success("b"))

    store = InMemoryHostPreferenceStore()
    reg = FetchProviderRegistry([p1, p2], chain=["a", "b"], host_pref_store=store)
    await reg.fetch("https://example.com")

    # The default first-tier winning is the natural baseline; don't pollute
    # the cache with it.
    assert await store.get("example.com") is None


@pytest.mark.asyncio
async def test_stale_learned_preference_is_forgotten_on_total_failure():
    p1 = FakeProvider("a", return_value=_failure("a"))
    p2 = FakeProvider("b", return_value=_failure("b"))

    store = InMemoryHostPreferenceStore()
    await store.record("example.com", "b")

    reg = FetchProviderRegistry([p1, p2], chain=["a", "b"], host_pref_store=store)
    result = await reg.fetch("https://example.com")

    assert result.success is False
    # Learned preference should be cleared so we re-learn next time.
    assert await store.get("example.com") is None
