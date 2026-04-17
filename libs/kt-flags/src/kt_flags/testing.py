"""Pytest helpers for kt-flags.

``override_flags`` snapshots the currently-active OpenFeature provider,
installs an ``InMemoryProvider`` built from a ``{flag_key: value}`` mapping,
and restores the snapshot on exit. Nested ``override_flags`` blocks compose,
and a test that pre-swapped the provider keeps its pre-swap in place when
the override ends.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from openfeature.provider.in_memory_provider import InMemoryFlag, InMemoryProvider

from kt_flags.client import (
    _get_default_settings_provider,
    get_current_provider,
    get_flag_client,
    set_provider,
)


def make_memory_provider(values: dict[str, Any]) -> InMemoryProvider:
    """Build an ``InMemoryProvider`` from a flat ``{flag_key: value}`` mapping."""
    flags: dict[str, InMemoryFlag[Any]] = {
        key: InMemoryFlag(default_variant="default", variants={"default": value}) for key, value in values.items()
    }
    return InMemoryProvider(flags)


@contextmanager
def override_flags(values: dict[str, Any]) -> Iterator[None]:
    """Temporarily swap the default OpenFeature provider for in-memory values.

    Usage::

        with override_flags({"feature.full_text_fetch": False}):
            ...

    The previously-active provider is restored on exit. If no provider
    has been installed yet (first call in the test suite), the cached
    ``SettingsProvider`` is installed before the swap so exit can restore
    it cleanly.
    """
    get_flag_client()
    previous = get_current_provider()
    if previous is None:
        previous = _get_default_settings_provider()
        set_provider(previous)
    set_provider(make_memory_provider(values))
    try:
        yield
    finally:
        set_provider(previous)
