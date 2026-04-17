"""Read-only gate for graph-scoped writes.

A graph is read-only when ``Graph.read_only`` is True. Two sources:
- ``read_only_reason='owner'`` — owner toggled via settings UI.
- ``read_only_reason='migrating'`` — ``graph_migration_wf`` set it while
  running a type-version migration. The workflow itself bypasses this
  gate (it's the only code path allowed to mutate a migrating graph).
- ``read_only_reason='error'`` — a migration halted; the graph sits
  read-only until a superuser dispatches ``/re-migrate`` or clears
  the state manually.

Every write entry point (API mutation routes, ingest/decompose/node task
entries) calls :func:`assert_writable` before mutating graph-scoped data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kt_config.errors import GraphReadOnlyError

if TYPE_CHECKING:
    from kt_db.models import Graph


def assert_writable(graph: "Graph") -> None:
    """Raise :class:`GraphReadOnlyError` if the graph is marked read-only.

    No-op when ``graph.read_only`` is False. Accepts the ORM model directly
    so callers that already hold the row don't need an extra round-trip.
    """
    if graph.read_only:
        raise GraphReadOnlyError(
            graph_id=str(graph.id),
            reason=graph.read_only_reason or "unknown",
        )
