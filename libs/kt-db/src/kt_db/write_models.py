"""Write-optimized database models.

These models target a separate PostgreSQL instance optimized for fast writes.
All primary keys are deterministic TEXT strings (no UUID lookups needed).
No foreign key constraints — referential integrity is maintained by the
sync worker when it copies data to the graph-db.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class WriteBase(DeclarativeBase):
    """Separate metadata from the graph-db Base — targets the write database."""

    pass


class WriteRawSource(WriteBase):
    """Write-optimized raw source storage.

    Mirrors the graph-db RawSource table so that workers can read source
    content from write-db (behind pgbouncer) instead of hitting the
    graph-db connection pool during concurrent decomposition.
    """

    __tablename__ = "write_raw_sources"
    __table_args__ = (
        Index("ix_write_raw_sources_updated_at", "updated_at"),
        Index("ix_write_raw_sources_content_hash", "content_hash", unique=True),
        Index("ix_write_raw_sources_uri", "uri"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    uri: Mapped[str] = mapped_column(String(2000), nullable=False)
    title: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    raw_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_full_text: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider_id: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    fact_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    prohibited_chunk_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_super_source: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    fetch_attempted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    fetch_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class WritePageFetchLog(WriteBase):
    """Tracks which URLs have been fetched/processed to avoid re-fetching.

    Write-db equivalent of graph-db page_fetch_log.  Pipelines read and
    write this table; no FK constraints.
    """

    __tablename__ = "write_page_fetch_log"
    __table_args__ = (
        Index("ix_write_page_fetch_log_url", "url", unique=True),
        Index("ix_write_page_fetch_log_updated_at", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    url: Mapped[str] = mapped_column(String(2000), nullable=False)
    raw_source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fact_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    skip_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class WriteProhibitedChunk(WriteBase):
    """Tracks text chunks rejected by LLM safety filters during extraction."""

    __tablename__ = "write_prohibited_chunks"
    __table_args__ = (
        Index("ix_write_prohibited_chunks_updated_at", "updated_at"),
        Index("ix_write_prohibited_chunks_content_hash", "source_content_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    fallback_model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    fallback_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class WriteNode(WriteBase):
    __tablename__ = "write_nodes"
    __table_args__ = (
        Index("ix_write_nodes_updated_at", "updated_at"),
        Index("ix_write_nodes_node_type", "node_type"),
        Index("ix_write_nodes_node_uuid", "node_uuid", unique=True),
    )

    key: Mapped[str] = mapped_column(String(500), primary_key=True)
    node_uuid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    concept: Mapped[str] = mapped_column(String(500), nullable=False)
    node_type: Mapped[str] = mapped_column(String(20), nullable=False, default="concept")
    entity_subtype: Mapped[str | None] = mapped_column(String(20), nullable=True)
    parent_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_concept_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    definition: Mapped[str | None] = mapped_column(Text, nullable=True)
    definition_source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    attractor: Mapped[str | None] = mapped_column(String(500), nullable=True)
    filter_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    max_content_tokens: Mapped[int] = mapped_column(Integer, default=500)
    stale_after: Mapped[int] = mapped_column(Integer, default=30)
    fact_ids: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    enrichment_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    facts_at_last_build: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    visibility: Mapped[str] = mapped_column(String(20), default="public", server_default="public")
    creator_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class WriteEdge(WriteBase):
    __tablename__ = "write_edges"
    __table_args__ = (
        Index("ix_write_edges_updated_at", "updated_at"),
        Index("ix_write_edges_source_key", "source_node_key"),
        Index("ix_write_edges_target_key", "target_node_key"),
    )

    key: Mapped[str] = mapped_column(String(1200), primary_key=True)
    source_node_key: Mapped[str] = mapped_column(String(500), nullable=False)
    target_node_key: Mapped[str] = mapped_column(String(500), nullable=False)
    relationship_type: Mapped[str] = mapped_column(String(50), nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    weight_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    fact_ids: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class WriteDimension(WriteBase):
    __tablename__ = "write_dimensions"
    __table_args__ = (
        Index("ix_write_dimensions_updated_at", "updated_at"),
        Index("ix_write_dimensions_node_key", "node_key"),
    )

    key: Mapped[str] = mapped_column(String(800), primary_key=True)
    node_key: Mapped[str] = mapped_column(String(500), nullable=False)
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    suggested_concepts: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    batch_index: Mapped[int] = mapped_column(Integer, default=0)
    fact_count: Mapped[int] = mapped_column(Integer, default=0)
    is_definitive: Mapped[bool] = mapped_column(Boolean, default=False)
    fact_ids: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class WriteConvergenceReport(WriteBase):
    __tablename__ = "write_convergence_reports"
    __table_args__ = (Index("ix_write_convergence_updated_at", "updated_at"),)

    node_key: Mapped[str] = mapped_column(String(500), primary_key=True)
    convergence_score: Mapped[float] = mapped_column(Float, default=0.0)
    converged_claims: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    recommended_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class WriteDivergentClaim(WriteBase):
    __tablename__ = "write_divergent_claims"
    __table_args__ = (
        Index("ix_write_divergent_claims_node_key", "node_key"),
        Index("ix_write_divergent_claims_updated_at", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_key: Mapped[str] = mapped_column(String(500), nullable=False)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    model_positions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    divergence_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class WriteNodeCounter(WriteBase):
    __tablename__ = "write_node_counters"
    __table_args__ = (Index("ix_write_node_counters_updated_at", "updated_at"),)

    node_key: Mapped[str] = mapped_column(String(500), primary_key=True)
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    update_count: Mapped[int] = mapped_column(Integer, default=0)
    seed_fact_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class WriteFact(WriteBase):
    """Write-optimized fact storage.

    Uses UUID PK (not TEXT key) because fact identity is determined by
    embedding-based dedup (fuzzy), not by a deterministic key formula.
    The same UUID is shared across write-db, graph-db, and Qdrant.
    """

    __tablename__ = "write_facts"
    __table_args__ = (
        Index("ix_write_facts_updated_at", "updated_at"),
        Index("ix_write_facts_fact_type", "fact_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    fact_type: Mapped[str] = mapped_column(String(50), nullable=False)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    sources: Mapped[list["WriteFactSource"]] = relationship(
        back_populates="fact",
        lazy="raise",
        foreign_keys="[WriteFactSource.fact_id]",
        primaryjoin="WriteFact.id == foreign(WriteFactSource.fact_id)",
    )


class WriteFactSource(WriteBase):
    """Write-optimized fact-to-source provenance.

    Denormalizes raw source fields (uri, title, content_hash, provider_id)
    to avoid needing a separate write-db RawSource table.  The sync worker
    uses content_hash to find/create the corresponding graph-db RawSource.
    """

    __tablename__ = "write_fact_sources"
    __table_args__ = (
        Index("ix_write_fact_sources_updated_at", "updated_at"),
        Index("ix_write_fact_sources_fact_id", "fact_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    raw_source_uri: Mapped[str] = mapped_column(String(2000), nullable=False)
    raw_source_title: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    raw_source_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_source_provider_id: Mapped[str] = mapped_column(String(100), nullable=False)
    context_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    attribution: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_person: Mapped[str | None] = mapped_column(String(500), nullable=True)
    author_org: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    fact: Mapped["WriteFact"] = relationship(
        back_populates="sources",
        lazy="raise",
        foreign_keys="[WriteFactSource.fact_id]",
        primaryjoin="WriteFact.id == foreign(WriteFactSource.fact_id)",
    )


class WriteNodeFactRejection(WriteBase):
    """Tracks facts rejected as irrelevant for a node (write-db side)."""

    __tablename__ = "write_node_fact_rejections"
    __table_args__ = (
        Index("ix_write_nfr_updated_at", "updated_at"),
        Index("ix_write_nfr_node_id", "node_id"),
        Index("uq_write_nfr_node_fact", "node_id", "fact_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    fact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class WriteSeed(WriteBase):
    """A lightweight proto-node extracted from fact mentions.

    Seeds track entity/concept mentions during fact decomposition.
    When enough facts accumulate, a seed can be promoted to a full node.
    """

    __tablename__ = "write_seeds"
    __table_args__ = (
        Index("ix_write_seeds_updated_at", "updated_at"),
        Index("ix_write_seeds_status", "status"),
        Index("ix_write_seeds_seed_uuid", "seed_uuid", unique=True),
        Index("ix_write_seeds_phonetic_code", "phonetic_code"),
    )

    key: Mapped[str] = mapped_column(String(500), primary_key=True)
    seed_uuid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    node_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_subtype: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    merged_into_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    promoted_node_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    fact_count: Mapped[int] = mapped_column(Integer, default=0)
    phonetic_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    context_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class WriteSeedFact(WriteBase):
    """Many-to-many junction between seeds and facts."""

    __tablename__ = "write_seed_facts"
    __table_args__ = (
        Index("ix_write_seed_facts_updated_at", "updated_at"),
        Index("ix_write_seed_facts_seed_key", "seed_key"),
        Index("uq_wsf_seed_fact", "seed_key", "fact_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seed_key: Mapped[str] = mapped_column(String(500), nullable=False)
    fact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    extraction_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_role: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="mentioned",
        server_default="mentioned",
    )  # "mentioned" or "source_attribution"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class WriteEdgeCandidate(WriteBase):
    """Edge candidate fact from seed co-occurrence.

    One row per (seed_pair, fact).  Rejected facts stay permanently rejected;
    new facts for the same seed pair get status='pending' and are evaluated
    independently.
    """

    __tablename__ = "write_edge_candidates"
    __table_args__ = (
        Index("ix_wec_seed_a_status", "seed_key_a", "status"),
        Index("ix_wec_seed_b_status", "seed_key_b", "status"),
        UniqueConstraint("seed_key_a", "seed_key_b", "fact_id", name="uq_wec_pair_fact"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seed_key_a: Mapped[str] = mapped_column(String(500), nullable=False)
    seed_key_b: Mapped[str] = mapped_column(String(500), nullable=False)
    fact_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/accepted/rejected
    discovery_strategy: Mapped[str | None] = mapped_column(String(50), nullable=True)
    evaluation_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class WriteSeedMerge(WriteBase):
    """Audit trail for seed merges and splits."""

    __tablename__ = "write_seed_merges"
    __table_args__ = (Index("ix_write_seed_merges_updated_at", "updated_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation: Mapped[str] = mapped_column(String(10), nullable=False)
    source_seed_key: Mapped[str] = mapped_column(String(500), nullable=False)
    target_seed_key: Mapped[str] = mapped_column(String(500), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    fact_ids_moved: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class WriteSeedRoute(WriteBase):
    """Disambiguation pipe: maps an ambiguous parent seed to its children.

    When a seed is split into disambiguated children, route rows record
    the parent→child mapping. New mentions of the ambiguous name are routed
    through these pipes to the correct child based on contextual embedding.
    """

    __tablename__ = "write_seed_routes"
    __table_args__ = (
        UniqueConstraint("parent_seed_key", "child_seed_key", name="uq_wsr_parent_child"),
        Index("ix_wsr_parent_seed_key", "parent_seed_key"),
        Index("ix_wsr_child_seed_key", "child_seed_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parent_seed_key: Mapped[str] = mapped_column(String(500), nullable=False)
    child_seed_key: Mapped[str] = mapped_column(String(500), nullable=False)
    label: Mapped[str] = mapped_column(String(500), nullable=False)
    ambiguity_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="text",
    )  # "text" (same name, different entities) or "embedding" (different names, close embeddings)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class WriteNodeVersion(WriteBase):
    """Versioned snapshots for composite nodes (synthesis, perspective).

    Each version captures the state of a composite node at a point in time,
    including how many source nodes contributed.  The ``is_default`` flag
    marks the version currently served to readers.
    """

    __tablename__ = "write_node_versions"
    __table_args__ = (
        Index("ix_write_node_versions_node_key", "node_key"),
        Index("ix_write_node_versions_updated_at", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_key: Mapped[str] = mapped_column(String(500), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source_node_count: Mapped[int] = mapped_column(Integer, default=0)
    is_default: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class WriteLlmUsage(WriteBase):
    """Flat per-task LLM usage record.

    Each Hatchet task self-reports its own usage here, tagged with
    conversation/message/task_type/model. Aggregation happens at query
    time via SQL SUM ... GROUP BY.
    """

    __tablename__ = "write_llm_usage"
    __table_args__ = (
        Index("ix_write_llm_usage_updated_at", "updated_at"),
        Index("ix_write_llm_usage_conversation_id", "conversation_id"),
        Index("ix_write_llm_usage_message_id", "message_id"),
        Index("ix_write_llm_usage_task_type", "task_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    message_id: Mapped[str] = mapped_column(String(36), nullable=False)
    task_type: Mapped[str] = mapped_column(String(100), nullable=False)
    workflow_run_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class SyncWatermark(WriteBase):
    """Tracks the last synced timestamp per table for incremental sync.

    The graph_slug column scopes watermarks per-graph. NULL means the default graph.
    """

    __tablename__ = "sync_watermarks"

    table_name: Mapped[str] = mapped_column(String(100), primary_key=True)
    graph_slug: Mapped[str] = mapped_column(String(100), primary_key=True, default="default")
    last_synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class SyncFailure(WriteBase):
    """Dead-letter queue for records that persistently fail to sync.

    When a record fails to sync from write-db to graph-db, its key and table
    are recorded here with an error message. Exponential backoff prevents
    retrying too aggressively, and records exceeding max_retries are abandoned.
    """

    __tablename__ = "sync_failures"
    __table_args__ = (
        Index("ix_sync_failures_next_retry_at", "next_retry_at"),
        Index("ix_sync_failures_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    table_name: Mapped[str] = mapped_column(String(100), nullable=False)
    record_key: Mapped[str] = mapped_column(String(1200), nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, abandoned
    graph_slug: Mapped[str] = mapped_column(String(100), default="default")
    next_retry_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
