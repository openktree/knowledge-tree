"""Abstract base for entity extraction strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ExtractedEntity:
    """An entity/concept extracted from facts.

    No node_type — extractors return names and fact links only.
    Type classification is deferred to downstream metadata enrichment.
    """

    name: str
    fact_indices: list[int] = field(default_factory=list)  # 1-indexed
    aliases: list[str] = field(default_factory=list)


class EntityExtractor(ABC):
    """Strategy interface for entity/concept extraction from facts."""

    @abstractmethod
    async def extract(
        self,
        facts: list,
        *,
        scope: str = "",
    ) -> list[ExtractedEntity] | None:
        """Extract entities and concepts from a list of facts.

        Args:
            facts: List of fact objects with ``.content`` and ``.fact_type`` attributes.
            scope: Optional scope/topic hint for context.

        Returns:
            List of extracted entities, or None on total failure.
        """
