"""Unit tests for FetchProviderRegistry: chain order, preferred resolution, host learning."""

from __future__ import annotations

import pytest

from kt_providers.fetch.base import ContentFetcherProvider
from kt_providers.fetch.host_pref import InMemoryHostPreferenceStore
from kt_providers.fetch.registry import FetchProviderRegistry
from kt_providers.fetch.types import FetchResult
from kt_providers.fetch.url_safety import UnsafeUrlError


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
async def test_url_validator_short_circuits_chain_with_synthetic_attempt():
    """An UnsafeUrlError from the validator must be returned as a normal
    failed FetchResult — never raised — and no provider must be touched."""
    p1 = FakeProvider("a", return_value=_success("a"))

    async def reject(uri: str) -> None:
        raise UnsafeUrlError("nope")

    reg = FetchProviderRegistry([p1], chain=["a"], url_validator=reject)
    result = await reg.fetch("http://127.0.0.1/")

    assert result.success is False
    assert "unsafe URL" in (result.error or "")
    assert p1.calls == []  # provider never invoked
    assert len(result.attempts) == 1
    assert result.attempts[0].provider_id == "url_safety"
    assert result.attempts[0].success is False


@pytest.mark.asyncio
async def test_url_validator_passes_through_to_chain_when_url_is_safe():
    p1 = FakeProvider("a", return_value=_success("a"))

    async def allow(uri: str) -> None:
        return None

    reg = FetchProviderRegistry([p1], chain=["a"], url_validator=allow)
    result = await reg.fetch("https://example.com/")
    assert result.success is True
    assert p1.calls == ["https://example.com/"]


@pytest.mark.asyncio
async def test_is_available_exception_logged_with_traceback(caplog):
    """When a provider's is_available() raises, the error must surface in
    the attempts log AND the underlying traceback must hit the logger so
    operators have a chance to debug it."""
    import logging

    class BrokenAvailable(ContentFetcherProvider):
        @property
        def provider_id(self) -> str:
            return "broken"

        async def is_available(self) -> bool:
            raise RuntimeError("availability check exploded")

        async def fetch(self, uri: str) -> FetchResult:
            raise AssertionError("should never be called")

    p_good = FakeProvider("good", return_value=_success("good"))
    reg = FetchProviderRegistry([BrokenAvailable(), p_good], chain=["broken", "good"])

    with caplog.at_level(logging.WARNING, logger="kt_providers.fetch.registry"):
        result = await reg.fetch("https://example.com/")

    assert result.success is True
    assert result.provider_id == "good"
    # First attempt records the is_available failure with the underlying message.
    first = result.attempts[0]
    assert first.provider_id == "broken"
    assert first.success is False
    assert "availability check exploded" in (first.error or "")
    # And the traceback was actually emitted to the logger so an operator
    # can find the call site.
    assert any("is_available()" in record.getMessage() and record.exc_info is not None for record in caplog.records)


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


# ── Post-fetch hooks ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_fetch_hook_called_after_successful_fetch():
    """A post-fetch hook receives the URI and result and can enrich it."""
    hook_calls: list[tuple[str, FetchResult]] = []

    async def hook(uri: str, result: FetchResult) -> FetchResult:
        hook_calls.append((uri, result))
        meta = dict(result.html_metadata or {})
        meta["enriched"] = "yes"
        result.html_metadata = meta
        return result

    p1 = FakeProvider("a", return_value=_success("a"))
    reg = FetchProviderRegistry([p1], chain=["a"], post_fetch_hooks=[hook])
    result = await reg.fetch("https://example.com")

    assert result.success is True
    assert len(hook_calls) == 1
    assert hook_calls[0][0] == "https://example.com"
    assert result.html_metadata is not None
    assert result.html_metadata["enriched"] == "yes"


@pytest.mark.asyncio
async def test_post_fetch_hook_failure_does_not_break_result():
    """If a hook raises, the result from the provider is still returned."""

    async def bad_hook(uri: str, result: FetchResult) -> FetchResult:
        raise RuntimeError("hook exploded")

    p1 = FakeProvider("a", return_value=_success("a"))
    reg = FetchProviderRegistry([p1], chain=["a"], post_fetch_hooks=[bad_hook])
    result = await reg.fetch("https://example.com")

    assert result.success is True
    assert result.provider_id == "a"


@pytest.mark.asyncio
async def test_post_fetch_hooks_not_called_when_all_providers_fail():
    """Hooks only run on success — they should not be called on total failure."""
    hook_calls: list[str] = []

    async def hook(uri: str, result: FetchResult) -> FetchResult:
        hook_calls.append(uri)
        return result

    p1 = FakeProvider("a", return_value=_failure("a"))
    reg = FetchProviderRegistry([p1], chain=["a"], post_fetch_hooks=[hook])
    result = await reg.fetch("https://example.com")

    assert result.success is False
    assert hook_calls == []


@pytest.mark.asyncio
async def test_multiple_post_fetch_hooks_run_sequentially():
    """Multiple hooks run in order, each seeing the previous hook's result."""
    order: list[str] = []

    async def hook_a(uri: str, result: FetchResult) -> FetchResult:
        order.append("a")
        meta = dict(result.html_metadata or {})
        meta["hook_a"] = "done"
        result.html_metadata = meta
        return result

    async def hook_b(uri: str, result: FetchResult) -> FetchResult:
        order.append("b")
        # hook_b can see hook_a's enrichment
        assert result.html_metadata is not None
        assert result.html_metadata.get("hook_a") == "done"
        return result

    p1 = FakeProvider("a", return_value=_success("a"))
    reg = FetchProviderRegistry([p1], chain=["a"], post_fetch_hooks=[hook_a, hook_b])
    result = await reg.fetch("https://example.com")

    assert order == ["a", "b"]
