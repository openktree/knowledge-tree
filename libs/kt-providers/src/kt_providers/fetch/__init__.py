"""Pluggable content-fetcher providers with a configurable fallback chain.

The public surface of this package is a small registry/provider model that
mirrors the existing `KnowledgeProvider` / `ProviderRegistry` pattern in
``kt_providers``.  See ``kt_providers.fetch.registry.FetchProviderRegistry``
for the entry point used by workers, and ``kt_providers.fetch.builder``
for the DI helper that wires it from settings.
"""

from kt_providers.fetch.base import ContentFetcherProvider
from kt_providers.fetch.builder import build_fetch_registry
from kt_providers.fetch.file_data_store import FileDataStore
from kt_providers.fetch.host_pref import (
    HostPreferenceStore,
    InMemoryHostPreferenceStore,
    RedisHostPreferenceStore,
    host_of,
)
from kt_providers.fetch.registry import FetchProviderRegistry
from kt_providers.fetch.types import (
    MIN_EXTRACTED_LENGTH,
    FetchAttempt,
    FetchResult,
)

__all__ = [
    "MIN_EXTRACTED_LENGTH",
    "ContentFetcherProvider",
    "FetchAttempt",
    "FetchProviderRegistry",
    "FetchResult",
    "FileDataStore",
    "HostPreferenceStore",
    "InMemoryHostPreferenceStore",
    "RedisHostPreferenceStore",
    "build_fetch_registry",
    "host_of",
]
