"""Repository for persisting shell candidates to the plugin DB schema."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from kt_plugin_be_concept_extractor.strategies.hybrid import ShellCandidate

logger = logging.getLogger(__name__)

# Deterministic UUID5 namespace scoped to this plugin. Changing this value
# forces a new id space for every shell candidate and is therefore a
# breaking change — do not rotate without a migration plan.
_SHELL_NS = uuid.UUID("8f1d6a3c-4b2e-5f80-9c1d-70a6d4c1b2e3")

_INSERT_SQL = text(
    """
    INSERT INTO plugin_hybrid_extractor.shell_candidates
        (id, name, ner_label, source, fact_ids, scope)
    VALUES
        (:id, :name, :ner_label, :source, :fact_ids, :scope)
    ON CONFLICT ON CONSTRAINT uq_shell_scope_name_source DO NOTHING
    """
)


def shell_uuid(scope: str, name: str, source: str) -> uuid.UUID:
    """Derive the deterministic shell-candidate PK for a (scope, name, source)."""
    return uuid.uuid5(_SHELL_NS, f"{scope}|{name}|{source}")


class ShellCandidateRepository:
    """Writes rejected spaCy candidates to ``plugin_hybrid_extractor.shell_candidates``.

    Re-running ingestion for the same scope is a no-op thanks to the
    deterministic PK + unique constraint on (scope, name, source).
    """

    def __init__(self, session: "AsyncSession") -> None:
        self._session = session

    async def bulk_insert(
        self,
        candidates: list["ShellCandidate"],
        scope: str,
    ) -> None:
        """Insert shell candidates. Silently skips duplicates."""
        if not candidates:
            return

        rows = [
            {
                "id": str(shell_uuid(scope, c.name, c.source)),
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
