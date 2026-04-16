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

    def get_last_side_outputs(self) -> dict[str, list]:
        """Return side outputs captured during the last ``extract()`` call.

        Generic hook: keys are arbitrary string labels (e.g. ``"shells"``,
        ``"rejected"``); values are lists of plugin-defined objects. The
        pipeline iterates ``PostExtractionHook`` contributions matching each
        output key to persist them. Extractors with no side outputs return
        an empty dict.
        """
        return {}
