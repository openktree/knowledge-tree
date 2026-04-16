"""Knowledge-provider search contract: ABC + result types."""

from kt_core_engine_api.search.provider import KnowledgeProvider
from kt_core_engine_api.search.types import RawSearchResult

__all__ = [
    "KnowledgeProvider",
    "RawSearchResult",
]
