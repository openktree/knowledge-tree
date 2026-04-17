"""Agent state and context types."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Annotated, Any

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from qdrant_client import AsyncQdrantClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from kt_graph.read_engine import ReadGraphEngine
    from kt_graph.worker_engine import WorkerGraphEngine
    from kt_models.embeddings import EmbeddingService
    from kt_models.gateway import ModelGateway
    from kt_providers.fetch import FetchProviderRegistry, FileDataStore
    from kt_providers.registry import ProviderRegistry

    # Either engine type can be used as graph_engine
    GraphEngineType = WorkerGraphEngine | ReadGraphEngine

logger = logging.getLogger(__name__)

# Callback signature: async def callback(event_type: str, **data) -> None
EventCallback = Callable[..., Awaitable[None]]


class SynthesisState(BaseModel):
    """State for the Synthesis sub-agent."""

    query: str
    node_list: list[dict[str, str]] = Field(default_factory=list)  # [{"node_id": "...", "concept": "..."}, ...]
    facts_retrieved: dict[str, list[str]] = Field(default_factory=dict)
    answer: str = ""
    phase: str = "synthesizing"  # "synthesizing" | "done"
    messages: Annotated[Sequence[BaseMessage], add_messages] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}


class PipelineState(BaseModel):
    """Shared budget/tracking state for all pipeline agents.

    This is the base state for orchestrator, query, conversation, and ingest
    agents. It tracks budgets, visited/created nodes, and exploration path.
    Formerly ``OrchestratorState`` in worker-orchestrator.
    """

    query: str
    nav_budget: int = 20
    explore_budget: int = 2
    explore_used: int = 0
    nav_used: int = 0

    # Graph awareness (populated during scout phase)
    existing_concepts: list[dict] = Field(default_factory=list)  # type: ignore[type-arg]
    existing_perspectives: list[dict] = Field(default_factory=list)  # type: ignore[type-arg]

    # Tracking
    gathered_fact_count: int = 0
    visited_nodes: list[str] = Field(default_factory=list)
    created_nodes: list[str] = Field(default_factory=list)
    created_edges: list[str] = Field(default_factory=list)
    exploration_path: list[str] = Field(default_factory=list)

    # Sub-explorer briefings
    sub_explorer_summaries: list[dict] = Field(default_factory=list)  # type: ignore[type-arg]

    phase: str = "planning"  # planning | gathering | assembling | synthesizing
    answer: str = ""
    messages: Annotated[Sequence[BaseMessage], add_messages] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}

    @property
    def explore_remaining(self) -> int:
        """How many explore budget units remain."""
        return max(0, self.explore_budget - self.explore_used)

    def has_visited(self, node_id: str) -> bool:
        """Check if a node has already been visited."""
        return node_id in self.visited_nodes


class NodeEntry(BaseModel):
    """A node to build — shared tool input schema."""

    name: str = Field(description="Node name or label")
    node_type: str = Field(default="concept", description="One of: concept, entity, event, location")


class PerspectiveEntry(BaseModel):
    """A perspective to build as a thesis/antithesis pair."""

    claim: str = Field(description="Full propositional sentence (the thesis)")
    source_concept_id: str = Field(description="UUID of the concept node this perspective is about")
    antithesis: str | None = Field(default=None, description="Opposing claim (the antithesis)")


class AgentContext:
    """Dependencies injected into agent tools."""

    def __init__(
        self,
        graph_engine: GraphEngineType,
        provider_registry: ProviderRegistry,
        model_gateway: ModelGateway,
        embedding_service: EmbeddingService | None,
        session: AsyncSession | None,
        emit_event: EventCallback | None = None,
        fetch_registry: FetchProviderRegistry | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        file_data_store: FileDataStore | None = None,
        parent: AgentContext | None = None,
        pipeline_tracker: Any | None = None,
        write_session_factory: async_sessionmaker[AsyncSession] | None = None,
        qdrant_client: AsyncQdrantClient | None = None,
    ) -> None:
        self.graph_engine = graph_engine
        self.provider_registry = provider_registry
        self.model_gateway = model_gateway
        self.embedding_service = embedding_service
        self.session = session
        self._emit_event = emit_event
        self.fetch_registry = fetch_registry
        self.session_factory = session_factory
        self.write_session_factory = write_session_factory
        self.qdrant_client = qdrant_client
        self.last_activity_at: float = time.monotonic()
        self._parent = parent
        self.pipeline_tracker: Any | None = pipeline_tracker

        if file_data_store is None:
            from kt_providers.fetch import FileDataStore as _FDS

            file_data_store = _FDS()
        self.file_data_store: FileDataStore = file_data_store

    def create_child_context(self) -> AgentContext:
        """Create a child AgentContext with its own database session.

        The child gets a new session (and GraphEngine) from the session factory
        while sharing stateless services (model_gateway, provider_registry,
        embedding_service, content_fetcher, emit_event).

        Raises RuntimeError if no session_factory was provided (e.g. in tests).
        The caller is responsible for committing/closing the child session.
        """
        if self.write_session_factory is None:
            raise RuntimeError("Cannot create child context: no write_session_factory provided")

        from kt_graph.worker_engine import WorkerGraphEngine

        child_write_session = self.write_session_factory()

        child_graph_engine = WorkerGraphEngine(
            child_write_session,
            self.embedding_service,
            qdrant_client=self.qdrant_client,
        )

        return AgentContext(
            graph_engine=child_graph_engine,
            provider_registry=self.provider_registry,
            model_gateway=self.model_gateway,
            embedding_service=self.embedding_service,
            session=None,
            emit_event=self._emit_event,
            fetch_registry=self.fetch_registry,
            session_factory=self.session_factory,
            file_data_store=self.file_data_store,
            parent=self,
            pipeline_tracker=self.pipeline_tracker,
            write_session_factory=self.write_session_factory,
            qdrant_client=self.qdrant_client,
        )

    async def emit(self, event_type: str, **data: Any) -> None:
        """Fire-and-forget event emission. No-op when no callback is set.

        Also propagates ``last_activity_at`` up the parent chain so that
        watchdog timers on ancestor contexts see activity from child
        contexts (e.g. sub-explorer pipelines).
        """
        now = time.monotonic()
        self.last_activity_at = now
        # Propagate heartbeat up the parent chain
        parent = self._parent
        while parent is not None:
            parent.last_activity_at = now
            parent = parent._parent
        if self._emit_event is None:
            return
        try:
            await self._emit_event(event_type, **data)
        except Exception:
            logger.warning("Failed to emit event %s", event_type, exc_info=True)
