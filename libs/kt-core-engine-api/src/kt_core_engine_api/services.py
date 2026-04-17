"""Backstage-style CoreServices container + PipelineContext.

Hatchet tasks, providers, and migrations never construct engines themselves:
they call ``ctx.services.graph_engine(graph_id)`` and get back an instance
bound to the right schema + write-db + Qdrant collection + public-cache
bridge for that graph.

The container lives on ``WorkerState`` (libs/kt-hatchet/src/kt_hatchet/lifespan.py)
and is populated once at worker boot from the existing resolvers
(``GraphSessionResolver``, ``PluginRegistry``, ``ProviderRegistry``, ``ModelGateway``).
Tasks access it via ``ctx.lifespan.services``.

This module stays import-light — no SQLAlchemy / LLM / HTTP imports — so
providers and plugins can depend on it without pulling the whole stack.
Concrete engine / gateway types are referenced only through TYPE_CHECKING
string forward refs; the implementation in ``kt-hatchet`` resolves them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    # Forward refs only — kt-core-engine-api cannot import from kt-graph /
    # kt-db / kt-models / kt-qdrant without creating circular deps.
    from kt_config.plugin import GraphTypeComposition, PluginRegistry
    from kt_db.models import Graph


@runtime_checkable
class GraphConfig(Protocol):
    """Resolved per-graph configuration slice.

    Concrete implementation lives in ``kt_config.graph_config``. This
    protocol keeps the CoreServices surface decoupled from the resolver's
    implementation details so providers can type-hint against it without
    an import cycle.
    """

    graph_type_id: str
    graph_type_version: int
    composition: "GraphTypeComposition"

    def get(self, path: str, default: Any = None) -> Any: ...

    def for_phase(self, phase: str) -> dict[str, Any]: ...


@runtime_checkable
class CoreServices(Protocol):
    """Factory container — the single entry point every pipeline uses.

    Every ``graph_id`` argument accepts ``None`` to denote "operate on the
    default graph". The container is responsible for resolving the right
    schema, write-db, Qdrant collection, and public-cache bridge.
    """

    # ── singletons ────────────────────────────────────────────────────
    def gateway(self) -> Any:
        """Return the shared ``ModelGateway``."""

    def plugin_registry(self) -> "PluginRegistry":
        """Return the global plugin registry."""

    def config_resolver(self) -> Any:
        """Return the ``GraphConfigResolver`` instance."""

    # ── graph-bound accessors ────────────────────────────────────────
    def graph_engine(self, graph_id: uuid.UUID | None) -> Any:
        """Return a ``GraphEngine`` bound to this graph's sessions.

        When ``graph.use_public_cache`` is True, the returned engine has
        a ``PublicGraphBridge`` wired in; otherwise it runs without.
        """

    def write_engine(self, graph_id: uuid.UUID | None) -> Any:
        """Return a ``WriteEngine`` bound to this graph's write-db session factory."""

    def qdrant(self, graph_id: uuid.UUID | None) -> Any:
        """Return a Qdrant repository scoped to this graph's collections."""

    async def graph_config(self, graph_id: uuid.UUID | None) -> GraphConfig:
        """Return the resolved ``GraphConfig`` for this graph."""
        ...

    async def load_graph(self, graph_id: uuid.UUID | None) -> "Graph":
        """Return the ORM Graph row for this id (``None`` → default graph)."""

    # ── provider lookup ──────────────────────────────────────────────
    def provider(self, phase: str, provider_id: str) -> Any:
        """Resolve a named provider for one pipeline phase.

        ``phase`` is one of: ``"fact_decomposition"``, ``"disambiguation"``,
        ``"seed_multiplex"``, ``"seed_promotion"``, ``"dimensions"``,
        ``"definition"``, ``"relations"``, ``"sync"``, ``"source_cache"``,
        ``"source_contribution"``, ``"agentic_tasks"``. The providers
        themselves land in Phases 3–6; during Phase 1–2 this raises
        ``KeyError`` if asked for a phase that hasn't been extracted yet.
        """


@dataclass
class PipelineContext:
    """Per-task context. Built once at Hatchet task entry and threaded down.

    Providers receive this on every call and read from it via the
    convenience properties; they never reach into ``WorkerState`` directly.
    """

    graph_id: uuid.UUID | None
    services: CoreServices
    config: GraphConfig

    @property
    def composition(self) -> "GraphTypeComposition":
        return self.config.composition

    @property
    def gateway(self) -> Any:
        return self.services.gateway()

    def graph_engine(self) -> Any:
        return self.services.graph_engine(self.graph_id)

    def write_engine(self) -> Any:
        return self.services.write_engine(self.graph_id)

    def qdrant(self) -> Any:
        return self.services.qdrant(self.graph_id)

    def provider(self, phase: str, provider_id: str | None = None) -> Any:
        """Resolve a provider for ``phase`` using composition when ``provider_id`` omitted."""
        if provider_id is None:
            provider_id = getattr(self.composition, phase, None)
            if provider_id is None:
                raise KeyError(f"GraphTypeComposition has no field '{phase}'")
        return self.services.provider(phase, provider_id)
