import uuid
from datetime import UTC, datetime

from fastapi_users.db import SQLAlchemyBaseOAuthAccountTableUUID, SQLAlchemyBaseUserTableUUID
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    """Return current UTC time as a naive datetime (no tzinfo).

    asyncpg requires naive datetimes for TIMESTAMP WITHOUT TIME ZONE columns.
    We use datetime.now(UTC) for correctness, then strip tzinfo for DB compat.
    """
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    concept: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    attractor: Mapped[str | None] = mapped_column(String(500), nullable=True)
    filter_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    max_content_tokens: Mapped[int] = mapped_column(Integer, default=500)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
    node_type: Mapped[str] = mapped_column(String(20), default="concept", server_default="concept", index=True)
    entity_subtype: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True
    )
    source_concept_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True
    )
    stale_after: Mapped[int] = mapped_column(Integer, default=30, doc="Days until considered stale")
    update_count: Mapped[int] = mapped_column(Integer, default=0)
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    definition: Mapped[str | None] = mapped_column(Text, nullable=True)
    definition_source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    definition_generated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    enrichment_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    visibility: Mapped[str] = mapped_column(String(20), default="public", server_default="public")
    creator_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )

    def __init__(self, **kwargs: object) -> None:
        self._embedding: list[float] | None = kwargs.pop("embedding", None)  # type: ignore[assignment]
        super().__init__(**kwargs)

    @property
    def embedding(self) -> list[float] | None:
        """In-memory embedding (not persisted). Stored in Qdrant."""
        return getattr(self, "_embedding", None)

    @embedding.setter
    def embedding(self, value: list[float] | None) -> None:
        self._embedding = value

    # Relationships
    parent: Mapped["Node | None"] = relationship(remote_side="Node.id", foreign_keys=[parent_id])
    source_concept: Mapped["Node | None"] = relationship(remote_side="Node.id", foreign_keys=[source_concept_id])
    dimensions: Mapped[list["Dimension"]] = relationship(back_populates="node", cascade="all, delete-orphan")
    convergence_report: Mapped["ConvergenceReport | None"] = relationship(
        back_populates="node", uselist=False, cascade="all, delete-orphan"
    )
    node_facts: Mapped[list["NodeFact"]] = relationship(back_populates="node", cascade="all, delete-orphan")
    versions: Mapped[list["NodeVersion"]] = relationship(back_populates="node", cascade="all, delete-orphan")
    outgoing_edges: Mapped[list["Edge"]] = relationship(
        foreign_keys="Edge.source_node_id", back_populates="source_node", cascade="all, delete-orphan"
    )
    incoming_edges: Mapped[list["Edge"]] = relationship(
        foreign_keys="Edge.target_node_id", back_populates="target_node", cascade="all, delete-orphan"
    )


class NodeCounter(Base):
    """Separate counter table to avoid row-lock contention on nodes.

    Uses INSERT ON CONFLICT DO UPDATE for atomic increment without
    locking the node row itself.
    """

    __tablename__ = "node_counters"

    node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("nodes.id", ondelete="CASCADE"), primary_key=True
    )
    access_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    update_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class Edge(Base):
    __tablename__ = "edges"
    __table_args__ = (
        UniqueConstraint("source_node_id", "target_node_id", "relationship_type", name="uq_edge_source_target_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False
    )
    target_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False
    )
    relationship_type: Mapped[str] = mapped_column(String(50), nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=0.5)
    created_by_query: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("query_origins.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
    justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    weight_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)

    # Relationships
    source_node: Mapped["Node"] = relationship(foreign_keys=[source_node_id], back_populates="outgoing_edges")
    target_node: Mapped["Node"] = relationship(foreign_keys=[target_node_id], back_populates="incoming_edges")
    query_origin: Mapped["QueryOrigin | None"] = relationship(back_populates="edges")
    edge_facts: Mapped[list["EdgeFact"]] = relationship(back_populates="edge", cascade="all, delete-orphan")


class Dimension(Base):
    __tablename__ = "dimensions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False
    )
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    suggested_concepts: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    model_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    batch_index: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    fact_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_definitive: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    def __init__(self, **kwargs: object) -> None:
        self._embedding: list[float] | None = kwargs.pop("embedding", None)  # type: ignore[assignment]
        super().__init__(**kwargs)

    @property
    def embedding(self) -> list[float] | None:
        """In-memory embedding (not persisted). Stored in Qdrant."""
        return getattr(self, "_embedding", None)

    @embedding.setter
    def embedding(self, value: list[float] | None) -> None:
        self._embedding = value

    # Relationships
    node: Mapped["Node"] = relationship(back_populates="dimensions")
    dimension_facts: Mapped[list["DimensionFact"]] = relationship(
        back_populates="dimension", cascade="all, delete-orphan"
    )


class ConvergenceReport(Base):
    __tablename__ = "convergence_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    convergence_score: Mapped[float] = mapped_column(Float, default=0.0)
    converged_claims: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    recommended_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Relationships
    node: Mapped["Node"] = relationship(back_populates="convergence_report")
    divergent_claims: Mapped[list["DivergentClaim"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )


class DivergentClaim(Base):
    __tablename__ = "divergent_claims"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("convergence_reports.id", ondelete="CASCADE"), nullable=False
    )
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    model_positions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    divergence_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    analysis: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    report: Mapped["ConvergenceReport"] = relationship(back_populates="divergent_claims")


class Fact(Base):
    __tablename__ = "facts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    fact_type: Mapped[str] = mapped_column(String(50), nullable=False)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    def __init__(self, **kwargs: object) -> None:
        self._embedding: list[float] | None = kwargs.pop("embedding", None)  # type: ignore[assignment]
        super().__init__(**kwargs)

    @property
    def embedding(self) -> list[float] | None:
        """In-memory embedding (not persisted). Stored in Qdrant."""
        return getattr(self, "_embedding", None)

    @embedding.setter
    def embedding(self, value: list[float] | None) -> None:
        self._embedding = value

    # Relationships
    sources: Mapped[list["FactSource"]] = relationship(back_populates="fact", cascade="all, delete-orphan")
    node_facts: Mapped[list["NodeFact"]] = relationship(back_populates="fact", cascade="all, delete-orphan")
    edge_facts: Mapped[list["EdgeFact"]] = relationship(back_populates="fact", cascade="all, delete-orphan")


class FactSource(Base):
    __tablename__ = "fact_sources"
    __table_args__ = (UniqueConstraint("fact_id", "raw_source_id", name="uq_fact_source"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facts.id", ondelete="CASCADE"), nullable=False
    )
    raw_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_sources.id", ondelete="CASCADE"), nullable=False
    )
    context_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    attribution: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_person: Mapped[str | None] = mapped_column(String(500), nullable=True)
    author_org: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Relationships
    fact: Mapped["Fact"] = relationship(back_populates="sources")
    raw_source: Mapped["RawSource"] = relationship(back_populates="fact_sources")


class RawSource(Base):
    __tablename__ = "raw_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    uri: Mapped[str] = mapped_column(String(2000), nullable=False)
    title: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    raw_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    is_full_text: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider_id: Mapped[str] = mapped_column(String(100), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    provider_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    fact_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    prohibited_chunk_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_super_source: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    fetch_attempted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # Relationships
    fact_sources: Mapped[list["FactSource"]] = relationship(back_populates="raw_source", cascade="all, delete-orphan")
    prohibited_chunks: Mapped[list["ProhibitedChunk"]] = relationship(
        back_populates="raw_source", cascade="all, delete-orphan"
    )


class ProhibitedChunk(Base):
    """Text chunk rejected by LLM safety filters during fact extraction."""

    __tablename__ = "prohibited_chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    raw_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_sources.id", ondelete="CASCADE"), nullable=False
    )
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    fallback_model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Relationships
    raw_source: Mapped["RawSource"] = relationship(back_populates="prohibited_chunks")


class NodeFact(Base):
    __tablename__ = "node_facts"
    __table_args__ = (UniqueConstraint("node_id", "fact_id", name="uq_node_fact"),)

    node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("nodes.id", ondelete="CASCADE"), primary_key=True
    )
    fact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facts.id", ondelete="CASCADE"), primary_key=True
    )
    relevance_score: Mapped[float] = mapped_column(Float, default=1.0)
    stance: Mapped[str | None] = mapped_column(String(20), nullable=True)
    linked_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Relationships
    node: Mapped["Node"] = relationship(back_populates="node_facts")
    fact: Mapped["Fact"] = relationship(back_populates="node_facts")


class DimensionFact(Base):
    __tablename__ = "dimension_facts"
    __table_args__ = (UniqueConstraint("dimension_id", "fact_id", name="uq_dimension_fact"),)

    dimension_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dimensions.id", ondelete="CASCADE"), primary_key=True
    )
    fact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facts.id", ondelete="CASCADE"), primary_key=True
    )
    linked_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Relationships
    dimension: Mapped["Dimension"] = relationship(back_populates="dimension_facts")
    fact: Mapped["Fact"] = relationship()


class NodeFactRejection(Base):
    __tablename__ = "node_fact_rejections"
    __table_args__ = (
        UniqueConstraint("node_id", "fact_id", name="uq_node_fact_rejection"),
        Index("ix_node_fact_rejections_node_id", "node_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False
    )
    fact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facts.id", ondelete="CASCADE"), nullable=False
    )
    rejected_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class EdgeFact(Base):
    __tablename__ = "edge_facts"
    __table_args__ = (UniqueConstraint("edge_id", "fact_id", name="uq_edge_fact"),)

    edge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("edges.id", ondelete="CASCADE"), primary_key=True
    )
    fact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facts.id", ondelete="CASCADE"), primary_key=True
    )
    relevance_score: Mapped[float] = mapped_column(Float, default=1.0)
    linked_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Relationships
    edge: Mapped["Edge"] = relationship(back_populates="edge_facts")
    fact: Mapped["Fact"] = relationship(back_populates="edge_facts")


class NodeVersion(Base):
    __tablename__ = "node_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source_node_count: Mapped[int] = mapped_column(Integer, default=0)
    is_default: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Relationships
    node: Mapped["Node"] = relationship(back_populates="versions")


class ProviderFetch(Base):
    __tablename__ = "provider_fetches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_id: Mapped[str] = mapped_column(String(100), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    fetch_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class PageFetchLog(Base):
    """Tracks which URLs have been fetched and processed for fact decomposition.

    Used to skip already-processed pages across different queries, and to
    re-process pages after a configurable staleness window.
    """

    __tablename__ = "page_fetch_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    url: Mapped[str] = mapped_column(String(2000), nullable=False, unique=True, index=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_sources.id", ondelete="SET NULL"), nullable=True
    )
    fact_count: Mapped[int] = mapped_column(Integer, default=0)
    skip_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Relationships
    raw_source: Mapped["RawSource | None"] = relationship()


class AIModel(Base):
    __tablename__ = "ai_models"

    model_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    known_biases: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class QueryOrigin(Base):
    __tablename__ = "query_origins"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Relationships
    edges: Mapped[list["Edge"]] = relationship(back_populates="query_origin")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mode: Mapped[str] = mapped_column(String(20), default="research", server_default="research")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    messages: Mapped[list["ConversationMessage"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="ConversationMessage.turn_number"
    )


class IngestSource(Base):
    __tablename__ = "ingest_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "file" | "link"
    original_name: Mapped[str] = mapped_column(String(500), nullable=False)
    stored_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_sources.id", ondelete="SET NULL"), nullable=True
    )
    section_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Relationships
    conversation: Mapped["Conversation"] = relationship()
    raw_source: Mapped["RawSource | None"] = relationship()


# ---------------------------------------------------------------------------
# Auth models
# ---------------------------------------------------------------------------


class OAuthAccount(SQLAlchemyBaseOAuthAccountTableUUID, Base):
    """Stores OAuth provider tokens per user (e.g. Google)."""

    pass


class User(SQLAlchemyBaseUserTableUUID, Base):
    """Application user -- extends FastAPI Users base."""

    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    encrypted_openrouter_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    oauth_accounts: Mapped[list["OAuthAccount"]] = relationship("OAuthAccount", lazy="joined")


class SystemSetting(Base):
    """Key-value store for admin-configurable system settings."""

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class ApiToken(Base):
    """User-generated long-lived API tokens (for API/MCP access)."""

    __tablename__ = "api_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    token_hash: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


# ---------------------------------------------------------------------------


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    turn_number: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Assistant-only fields
    nav_budget: Mapped[int | None] = mapped_column(Integer, nullable=True)
    explore_budget: Mapped[int | None] = mapped_column(Integer, nullable=True)
    nav_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    explore_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    visited_nodes: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    created_nodes: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    created_edges: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    subgraph: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # "pending"|"running"|"completed"|"failed"
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    workflow_run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Relationships
    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
    research_report: Mapped["ResearchReport | None"] = relationship(
        back_populates="message", uselist=False, cascade="all, delete-orphan"
    )


class ResearchReport(Base):
    """Persisted outcome summary for an orchestrator run.

    Written at the end of the orchestrate task so that research value
    (nodes, edges, budget) is queryable even after Hatchet runs are pruned.
    """

    __tablename__ = "research_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversation_messages.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Outcome counts
    nodes_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    edges_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    waves_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Budget
    explore_budget: Mapped[int | None] = mapped_column(Integer, nullable=True)
    explore_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    nav_budget: Mapped[int | None] = mapped_column(Integer, nullable=True)
    nav_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Human-readable scope summaries (one per sub-explorer scope)
    scope_summaries: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)

    # Token / cost tracking
    total_prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    total_completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    total_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0.0")
    usage_by_task: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Super sources deferred during exploration (JSONB list of {uri, title, estimated_tokens, ...})
    super_sources: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Report type: "research", "graph_builder", "ingestion"
    report_type: Mapped[str] = mapped_column(String(30), nullable=False, default="research", server_default="research")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Relationships
    message: Mapped["ConversationMessage"] = relationship(back_populates="research_report")
    usage_records: Mapped[list["LlmUsageRecord"]] = relationship(
        back_populates="research_report", cascade="all, delete-orphan"
    )


class LlmUsageRecord(Base):
    """Per-model token usage record linked to a research report (legacy)."""

    __tablename__ = "llm_usage_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    research_report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_reports.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Relationships
    research_report: Mapped["ResearchReport"] = relationship(back_populates="usage_records")


class LlmUsage(Base):
    """Flat per-task LLM usage record.

    Each Hatchet task self-reports its own usage, tagged with
    conversation/message/task_type/model. Aggregation at query time.
    No FKs — keeps it simple and avoids ordering issues during sync.
    """

    __tablename__ = "llm_usage"
    __table_args__ = (
        Index("ix_llm_usage_conversation_id", "conversation_id"),
        Index("ix_llm_usage_message_id", "message_id"),
        Index("ix_llm_usage_task_type", "task_type"),
        Index("ix_llm_usage_model_id", "model_id"),
        Index("ix_llm_usage_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    task_type: Mapped[str] = mapped_column(String(100), nullable=False)
    workflow_run_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


# ── Synthesis document models ─────────────────────────────────────────


class SynthesisSentence(Base):
    """A sentence in a synthesis/supersynthesis document."""

    __tablename__ = "synthesis_sentences"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    synthesis_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sentence_text: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Relationships
    fact_links: Mapped[list["SentenceFact"]] = relationship(back_populates="sentence", cascade="all, delete-orphan")
    node_links: Mapped[list["SentenceNodeLink"]] = relationship(
        back_populates="sentence", cascade="all, delete-orphan"
    )


class SentenceFact(Base):
    """Links a synthesis sentence to a fact by embedding distance."""

    __tablename__ = "sentence_facts"

    sentence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("synthesis_sentences.id", ondelete="CASCADE"), primary_key=True
    )
    fact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facts.id", ondelete="CASCADE"), primary_key=True
    )
    embedding_distance: Mapped[float] = mapped_column(Float, nullable=False)

    # Relationships
    sentence: Mapped["SynthesisSentence"] = relationship(back_populates="fact_links")
    fact: Mapped["Fact"] = relationship()


class SentenceNodeLink(Base):
    """Links a synthesis sentence to a node (by name/alias text match)."""

    __tablename__ = "sentence_node_links"

    sentence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("synthesis_sentences.id", ondelete="CASCADE"), primary_key=True
    )
    node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("nodes.id", ondelete="CASCADE"), primary_key=True
    )
    link_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "name_match" | "alias_match"

    # Relationships
    sentence: Mapped["SynthesisSentence"] = relationship(back_populates="node_links")
    node: Mapped["Node"] = relationship()


class SynthesisChild(Base):
    """Links a supersynthesis to its child synthesis nodes."""

    __tablename__ = "synthesis_children"

    supersynthesis_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("nodes.id", ondelete="CASCADE"), primary_key=True
    )
    synthesis_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("nodes.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
