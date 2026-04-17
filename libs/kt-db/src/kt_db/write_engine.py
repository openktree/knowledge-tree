"""WriteEngine — per-graph handle for write-db session + repositories.

Thin counterpart to ``GraphEngine`` (which wraps the graph-db read path).
Every pipeline write goes through a ``WriteEngine`` bound to the running
graph, so callers never have to reason about which schema / connection
the session actually targets — they ask ``ctx.write_engine()`` and get
the right one.

``CoreServices.write_engine(graph_id)`` constructs one of these per task
using the existing ``WriteSessionFactory`` held by ``GraphSessionResolver``.
No state lives on the object itself; it's effectively a typed bundle of
session factory + graph_id + pre-built repositories so providers don't
have to import every repository class individually.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator, Callable

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from kt_db.repositories.write_dimensions import WriteDimensionRepository
    from kt_db.repositories.write_edges import WriteEdgeRepository
    from kt_db.repositories.write_facts import WriteFactRepository
    from kt_db.repositories.write_nodes import WriteNodeRepository
    from kt_db.repositories.write_seeds import WriteSeedRepository


WriteSessionFactory = Callable[[], "AsyncSession"]


class WriteEngine:
    """Per-graph write-db session + repository bundle.

    Not a long-lived object — constructed fresh per Hatchet task by
    ``CoreServices.write_engine(graph_id)``. Callers open a session via
    :meth:`session` and pass it into the repository factory methods.

    Pipeline code prefers the ``async with engine.session()`` form so the
    commit/rollback boundary is explicit and testable.
    """

    __slots__ = ("graph_id", "_session_factory")

    def __init__(self, graph_id: uuid.UUID | None, session_factory: WriteSessionFactory) -> None:
        self.graph_id = graph_id
        self._session_factory = session_factory

    @asynccontextmanager
    async def session(self) -> AsyncIterator["AsyncSession"]:
        """Open a write-db session scoped to this graph."""
        async with self._session_factory() as session:
            yield session

    # ── Repository factories ─────────────────────────────────────────
    # Thin wrappers so providers don't need to import every repo class.
    # Each takes the open session as argument (idiomatic for SQLAlchemy
    # async — repos are stateless; the session carries the transaction).

    def nodes(self, session: "AsyncSession") -> "WriteNodeRepository":
        from kt_db.repositories.write_nodes import WriteNodeRepository

        return WriteNodeRepository(session)

    def edges(self, session: "AsyncSession") -> "WriteEdgeRepository":
        from kt_db.repositories.write_edges import WriteEdgeRepository

        return WriteEdgeRepository(session)

    def facts(self, session: "AsyncSession") -> "WriteFactRepository":
        from kt_db.repositories.write_facts import WriteFactRepository

        return WriteFactRepository(session)

    def dimensions(self, session: "AsyncSession") -> "WriteDimensionRepository":
        from kt_db.repositories.write_dimensions import WriteDimensionRepository

        return WriteDimensionRepository(session)

    def seeds(self, session: "AsyncSession") -> "WriteSeedRepository":
        from kt_db.repositories.write_seeds import WriteSeedRepository

        return WriteSeedRepository(session)
