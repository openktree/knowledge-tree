"""Registry for ontology providers.

Mirrors providers/registry.py — provides registration and lookup of
ontology providers by ID.
"""

from __future__ import annotations

from kt_ontology.base import OntologyProvider


class OntologyProviderRegistry:
    """Registry for ontology providers."""

    def __init__(self) -> None:
        self._providers: dict[str, OntologyProvider] = {}
        self._default_id: str | None = None

    def register(self, provider: OntologyProvider, *, default: bool = False) -> None:
        """Register a provider. If default=True, set it as the default."""
        self._providers[provider.provider_id] = provider
        if default or self._default_id is None:
            self._default_id = provider.provider_id

    def get(self, provider_id: str) -> OntologyProvider | None:
        return self._providers.get(provider_id)

    def get_default(self) -> OntologyProvider | None:
        if self._default_id is None:
            return None
        return self._providers.get(self._default_id)

    def get_all(self) -> list[OntologyProvider]:
        return list(self._providers.values())
