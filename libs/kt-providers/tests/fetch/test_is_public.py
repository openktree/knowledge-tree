"""Tests for the ``is_public`` provider classification used by the multigraph
public-graph cache + contribution machinery.

The contract:
- Built-in fetch providers default to ``is_public = True``.
- A subclass can override the class attribute to ``False`` (the
  jira/sharepoint/etc. case).
- ``build_fetch_registry`` applies ``Settings.fetch_provider_public_overrides``
  per-instance, so an operator can flip a normally-public provider private
  without subclassing (e.g. ``httpx`` pointed at an intranet).
- The ``FetchProviderRegistry`` propagates the *winning* provider's
  ``is_public`` onto ``FetchResult.is_public`` so downstream code can decide
  whether to consult the public graph cache or contribute results upstream.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kt_providers.fetch.base import ContentFetcherProvider
from kt_providers.fetch.builder import build_fetch_registry
from kt_providers.fetch.registry import FetchProviderRegistry
from kt_providers.fetch.types import FetchResult


class _StubProvider(ContentFetcherProvider):
    def __init__(self, pid: str, *, success: bool = True) -> None:
        self._pid = pid
        self._success = success

    @property
    def provider_id(self) -> str:
        return self._pid

    async def is_available(self) -> bool:
        return True

    async def fetch(self, uri: str) -> FetchResult:
        if self._success:
            return FetchResult(uri=uri, content="x" * 200)
        return FetchResult(uri=uri, error="boom")


class _PrivateProvider(_StubProvider):
    """Models a future tenant connector (jira/sharepoint/...) that declares
    itself non-public at the class level."""

    is_public = False


def test_default_fetch_provider_is_public():
    p = _StubProvider("public")
    assert p.is_public is True


def test_subclass_can_declare_itself_private():
    p = _PrivateProvider("private")
    assert p.is_public is False


def test_instance_override_does_not_leak_to_class():
    """Operator overrides patch the instance, never the class — important so
    test doubles, parallel test workers, and process-local registries cannot
    contaminate each other."""
    p = _StubProvider("public")
    p.is_public = False
    assert p.is_public is False
    # A fresh instance still sees the class default.
    assert _StubProvider("other").is_public is True


@pytest.mark.asyncio
async def test_registry_propagates_is_public_on_success():
    public = _StubProvider("public")
    reg = FetchProviderRegistry([public], chain=["public"])

    result = await reg.fetch("https://example.com")

    assert result.success is True
    assert result.provider_id == "public"
    assert result.is_public is True


@pytest.mark.asyncio
async def test_registry_propagates_private_classification():
    private = _PrivateProvider("private")
    reg = FetchProviderRegistry([private], chain=["private"])

    result = await reg.fetch("https://example.com")

    assert result.success is True
    assert result.provider_id == "private"
    assert result.is_public is False


@pytest.mark.asyncio
async def test_registry_uses_winning_provider_classification_not_first():
    """When the chain falls back, the public flag must reflect the provider
    that *actually* served the content — otherwise we could classify a private
    fetch as public just because a public provider was earlier in the chain."""
    failing_public = _StubProvider("public", success=False)
    private_winner = _PrivateProvider("private")
    reg = FetchProviderRegistry(
        [failing_public, private_winner],
        chain=["public", "private"],
    )

    result = await reg.fetch("https://example.com")

    assert result.success is True
    assert result.provider_id == "private"
    assert result.is_public is False


def _fake_settings(**overrides: object) -> SimpleNamespace:
    """Minimal settings stub for ``build_fetch_registry``.

    The builder uses ``getattr(settings, name, default)`` for most fields, so
    a ``SimpleNamespace`` with the handful of strictly-required attributes is
    enough — we don't need a real ``Settings`` here, which would otherwise
    pull in env vars and YAML loading from the test environment.
    """
    base: dict[str, object] = {
        "full_text_fetch_timeout": 10.0,
        "full_text_fetch_max_urls": 4,
        "fetch_user_agent": "test-agent",
        "fetch_provider_chain": "doi,httpx",
        "fetch_host_overrides": {},
        "fetch_provider_public_overrides": {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _provider(reg: FetchProviderRegistry, pid: str) -> ContentFetcherProvider:
    """Test helper: assert a provider is registered and return it via the
    public ``get_provider`` accessor (avoids reaching into ``_providers``)."""
    p = reg.get_provider(pid)
    assert p is not None, f"expected provider {pid!r} to be registered"
    return p


def test_builder_applies_public_overrides_per_instance():
    settings = _fake_settings(
        fetch_provider_public_overrides={"httpx": False},
    )
    registry = build_fetch_registry(settings)

    # The override only patches *this* registry's instance — fresh providers
    # built without the override stay public.
    fresh = build_fetch_registry(_fake_settings())
    assert _provider(registry, "httpx").is_public is False
    assert _provider(fresh, "httpx").is_public is True
    # Untouched providers keep their default classification.
    assert _provider(registry, "doi").is_public is True


def test_builder_ignores_overrides_for_unknown_provider_ids():
    settings = _fake_settings(
        fetch_provider_public_overrides={"nonexistent": False},
    )
    # Should not raise — unknown ids are silently ignored so a stale config
    # entry doesn't break worker startup.
    registry = build_fetch_registry(settings)
    # All registered providers keep their default (public) classification.
    for pid in ("doi", "httpx"):
        assert _provider(registry, pid).is_public is True


@pytest.mark.asyncio
async def test_failed_fetch_leaves_is_public_none():
    """If nothing succeeds, ``is_public`` stays None — callers must treat that
    as "unknown / do not cache"."""
    p1 = _StubProvider("a", success=False)
    p2 = _StubProvider("b", success=False)
    reg = FetchProviderRegistry([p1, p2], chain=["a", "b"])

    result = await reg.fetch("https://example.com")

    assert result.success is False
    assert result.is_public is None
