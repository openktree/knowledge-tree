"""Repository for persisting shell candidates to the plugin DB schema."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from kt_plugin_be_hybrid_extractor.extractor import ShellCandidate

logger = logging.getLogger(__name__)

_INSERT_SQL = text(
    """
    INSERT INTO plugin_hybrid_extractor.shell_candidates
        (id, name, ner_label, source, fact_ids, scope)
    VALUES
        (:id, :name, :ner_label, :source, :fact_ids, :scope)
    ON CONFLICT DO NOTHING
    """
)


class ShellCandidateRepository:
    """Writes rejected spaCy candidates to ``plugin_hybrid_extractor.shell_candidates``."""

    def __init__(self, session: "AsyncSession") -> None:
        self._session = session

    async def bulk_insert(
        self,
        candidates: list["ShellCandidate"],
        scope: str,
    ) -> None:
        """Insert shell candidates. Silently skips duplicates (ON CONFLICT DO NOTHING)."""
        if not candidates:
            return

        rows = [
            {
                "id": str(uuid.uuid4()),
                "name": c.name,
                "ner_label": c.ner_label,
                "source": c.source,
                "fact_ids": c.fact_ids,
                "scope": scope,
            }
            for c in candidates
        ]

        await self._session.execute(_INSERT_SQL, rows)
        logger.debug("Inserted %d shell candidates (scope=%r)", len(rows), scope)
