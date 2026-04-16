"""Thin wrapper around OpenFeature's ``InMemoryProvider`` for tests.

``make_memory_provider({"feature.x": True})`` builds a provider with one
variant per flag. Pattern matches ``override_flags`` in ``kt_flags.testing``.
"""

from __future__ import annotations

from typing import Any

from openfeature.provider.in_memory_provider import (
    InMemoryFlag,
    InMemoryProvider,
)

__all__ = ["InMemoryProvider", "make_memory_provider"]


def make_memory_provider(values: dict[str, Any]) -> InMemoryProvider:
    """Build an ``InMemoryProvider`` from a flat ``{flag_key: value}`` mapping."""
    flags: dict[str, InMemoryFlag[Any]] = {
        key: InMemoryFlag(default_variant="default", variants={"default": value}) for key, value in values.items()
    }
    return InMemoryProvider(flags)
