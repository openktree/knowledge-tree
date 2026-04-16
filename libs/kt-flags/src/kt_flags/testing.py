"""Pytest helpers for kt-flags.

``override_flags`` swaps the global OpenFeature provider for an
``InMemoryProvider`` built from a ``{flag_key: value}`` mapping, then
restores the previous provider on exit. Mirrors the ``plugin_registry_clean``
shape used in ``kt_config.testing`` so both helpers compose in a shared
``conftest.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from openfeature import api as _ofa

from kt_flags.client import get_flag_client
from kt_flags.providers.memory_provider import make_memory_provider
from kt_flags.providers.settings_provider import SettingsProvider


@contextmanager
def override_flags(values: dict[str, Any]) -> Iterator[None]:
    """Temporarily swap the default OpenFeature provider for in-memory values.

    Usage::

        with override_flags({"feature.full_text_fetch": False}):
            ...

    On exit the provider is restored to a fresh ``SettingsProvider`` so
    subsequent tests see production behavior. The cached ``FlagClient``
    wrapper is preserved — ``OpenFeatureClient`` resolves the active
    provider on each call, so swapping providers is enough.
    """
    # Ensure the default-provider latch is flipped before we override, so
    # an in-between ``get_flag_client()`` doesn't reinstate SettingsProvider
    # on top of our in-memory provider.
    get_flag_client()
    _ofa.set_provider(make_memory_provider(values))
    try:
        yield
    finally:
        _ofa.set_provider(SettingsProvider())


@pytest.fixture
def flag_overrides() -> Iterator[Any]:
    """Factory fixture — call with a dict to override flags for the test."""

    active: list[None] = []

    def _apply(values: dict[str, Any]) -> None:
        ctx = override_flags(values)
        ctx.__enter__()
        active.append(None)
        # Register finaliser via request? Simpler: cleanup in teardown below.
        # We stash ``ctx`` in a closure so teardown can exit it.
        _apply.__dict__.setdefault("_ctxs", []).append(ctx)

    try:
        yield _apply
    finally:
        for ctx in _apply.__dict__.get("_ctxs", []):
            ctx.__exit__(None, None, None)
