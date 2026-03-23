"""Ontology ancestry system — provider abstraction, caching, and merging pipeline."""

from kt_ontology.base import AncestorEntry, AncestryChain, OntologyProvider
from kt_ontology.registry import OntologyProviderRegistry

__all__ = [
    "AncestorEntry",
    "AncestryChain",
    "OntologyProvider",
    "OntologyProviderRegistry",
]
