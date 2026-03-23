"""Ontology provider abstraction and data models.

Mirrors the KnowledgeProvider pattern in providers/base.py — an abstract
base class that any ontology source (Wikidata, DBpedia, custom) can implement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel


class AncestorEntry(BaseModel):
    """A single ancestor in an ancestry chain."""

    name: str
    description: str | None = None
    external_id: str | None = None  # e.g. "Q12345" for Wikidata


class AncestryChain(BaseModel):
    """Ordered list from specific -> general (leaf -> root)."""

    ancestors: list[AncestorEntry]
    source: str  # provider_id


class OntologyProvider(ABC):
    """Abstract base class for ontology providers."""

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Unique identifier for this provider."""
        ...

    @abstractmethod
    async def get_ancestry(
        self, concept_name: str, node_type: str
    ) -> AncestryChain | None:
        """Return an ancestry chain for the given concept, or None if not found."""
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if the provider is currently available."""
        ...
