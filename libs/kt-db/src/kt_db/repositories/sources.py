import hashlib
import uuid
from datetime import datetime

from sqlalchemy import Date, case, cast, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from kt_config.types import RawSearchResult
from kt_db.models import Fact, FactSource, ProhibitedChunk, RawSource


class SourceRepository:
    """Repository for RawSource CRUD with content-hash deduplication."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def compute_hash(content: str) -> str:
        """Compute SHA-256 hash of content for deduplication."""
        return hashlib.sha256(content.encode()).hexdigest()

    async def get_by_hash(self, content_hash: str) -> RawSource | None:
        """Find a RawSource by its content hash."""
        result = await self._session.execute(select(RawSource).where(RawSource.content_hash == content_hash))
        return result.scalar_one_or_none()

    async def get_by_id(self, source_id: uuid.UUID) -> RawSource | None:
        """Find a RawSource by its ID."""
        result = await self._session.execute(select(RawSource).where(RawSource.id == source_id))
        return result.scalar_one_or_none()

    async def create_or_get(self, search_result: RawSearchResult) -> tuple[RawSource, bool]:
        """Create a new RawSource or return existing one if content hash matches.

        Uses INSERT ... ON CONFLICT DO NOTHING on the unique content_hash
        index, then fetches by hash to determine whether a new row was created.

        Returns:
            Tuple of (source, created) where created is True if a new record was inserted.
        """
        from kt_db.keys import uri_to_source_id

        content_hash = self.compute_hash(search_result.raw_content)
        new_id = uri_to_source_id(search_result.uri)

        stmt = pg_insert(RawSource).values(
            id=new_id,
            uri=search_result.uri,
            title=search_result.title,
            raw_content=search_result.raw_content,
            content_hash=content_hash,
            provider_id=search_result.provider_id,
            provider_metadata=search_result.provider_metadata,
        )
        # Use DO UPDATE (no-op) instead of DO NOTHING to avoid deadlocks
        # when concurrent transactions insert rows with the same content_hash.
        # DO NOTHING takes a ShareLock that can deadlock; DO UPDATE takes an
        # exclusive lock that serializes properly.
        stmt = stmt.on_conflict_do_update(
            index_elements=["content_hash"],
            set_={"content_hash": stmt.excluded.content_hash},
        ).returning(RawSource.id, text("xmax"))
        result = await self._session.execute(stmt)
        row = result.one()
        returned_id = row[0]
        # PostgreSQL: xmax == 0 means a fresh INSERT; xmax > 0 means
        # ON CONFLICT triggered an UPDATE on an existing row.
        created = int(row[1]) == 0

        source = await self.get_by_id(returned_id)
        assert source is not None  # noqa: S101
        return source, created

    async def get_by_uri(self, uri: str) -> RawSource | None:
        """Find a RawSource by its URI."""
        result = await self._session.execute(select(RawSource).where(RawSource.uri == uri))
        return result.scalar_one_or_none()

    async def update_content(
        self,
        source_id: uuid.UUID,
        new_content: str,
        is_full_text: bool = True,
        content_type: str | None = None,
    ) -> bool:
        """Replace raw_content with full-text content and update content_hash.

        Returns:
            True if the update was applied, False if skipped because another
            record already has the same content_hash.
        """
        new_hash = self.compute_hash(new_content)

        # Check if another record already has this hash (different URL, same content).
        existing = await self.get_by_hash(new_hash)
        if existing is not None and existing.id != source_id:
            return False

        values: dict[str, object] = {
            "raw_content": new_content,
            "content_hash": new_hash,
            "is_full_text": is_full_text,
        }
        if content_type is not None:
            values["content_type"] = content_type
        await self._session.execute(update(RawSource).where(RawSource.id == source_id).values(**values))
        await self._session.flush()
        return True

    async def list_sources(
        self,
        *,
        offset: int = 0,
        limit: int = 20,
        search: str | None = None,
        provider_id: str | None = None,
        sort_by: str | None = None,
        has_prohibited: bool | None = None,
        is_super_source: bool | None = None,
        fetch_status: str | None = None,
    ) -> list[RawSource]:
        """List raw sources with pagination and optional filters."""
        stmt = select(RawSource)

        # Sorting
        if sort_by == "fact_count":
            stmt = stmt.order_by(RawSource.fact_count.desc())
        elif sort_by == "prohibited_chunks":
            stmt = stmt.order_by(RawSource.prohibited_chunk_count.desc())
        else:
            stmt = stmt.order_by(RawSource.retrieved_at.desc())

        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(RawSource.title.ilike(pattern) | RawSource.uri.ilike(pattern))
        if provider_id:
            stmt = stmt.where(RawSource.provider_id == provider_id)
        if has_prohibited is True:
            stmt = stmt.where(RawSource.prohibited_chunk_count > 0)
        elif has_prohibited is False:
            stmt = stmt.where(RawSource.prohibited_chunk_count == 0)
        if is_super_source is True:
            stmt = stmt.where(RawSource.is_super_source.is_(True))
        elif is_super_source is False:
            stmt = stmt.where(RawSource.is_super_source.is_(False))
        if fetch_status == "full_text":
            stmt = stmt.where(RawSource.is_full_text.is_(True))
        elif fetch_status == "fetch_failed":
            stmt = stmt.where(RawSource.is_full_text.is_(False), RawSource.fetch_attempted.is_(True))
        elif fetch_status == "snippet":
            stmt = stmt.where(RawSource.is_full_text.is_(False), RawSource.fetch_attempted.is_(False))
        stmt = stmt.offset(offset).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_sources(
        self,
        *,
        search: str | None = None,
        provider_id: str | None = None,
        has_prohibited: bool | None = None,
        is_super_source: bool | None = None,
        fetch_status: str | None = None,
    ) -> int:
        """Count raw sources with optional filters."""
        stmt = select(func.count(RawSource.id))
        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(RawSource.title.ilike(pattern) | RawSource.uri.ilike(pattern))
        if provider_id:
            stmt = stmt.where(RawSource.provider_id == provider_id)
        if has_prohibited is True:
            stmt = stmt.where(RawSource.prohibited_chunk_count > 0)
        elif has_prohibited is False:
            stmt = stmt.where(RawSource.prohibited_chunk_count == 0)
        if is_super_source is True:
            stmt = stmt.where(RawSource.is_super_source.is_(True))
        elif is_super_source is False:
            stmt = stmt.where(RawSource.is_super_source.is_(False))
        if fetch_status == "full_text":
            stmt = stmt.where(RawSource.is_full_text.is_(True))
        elif fetch_status == "fetch_failed":
            stmt = stmt.where(RawSource.is_full_text.is_(False), RawSource.fetch_attempted.is_(True))
        elif fetch_status == "snippet":
            stmt = stmt.where(RawSource.is_full_text.is_(False), RawSource.fetch_attempted.is_(False))
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def get_fact_counts(self, source_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        """Get fact counts for a list of source IDs."""
        if not source_ids:
            return {}
        stmt = (
            select(FactSource.raw_source_id, func.count(FactSource.fact_id))
            .where(FactSource.raw_source_id.in_(source_ids))
            .group_by(FactSource.raw_source_id)
        )
        result = await self._session.execute(stmt)
        return dict(result.all())  # type: ignore[arg-type]

    async def increment_fact_count(self, source_id: uuid.UUID, delta: int = 1) -> None:
        """Increment the cached fact_count on a RawSource."""
        await self._session.execute(
            update(RawSource).where(RawSource.id == source_id).values(fact_count=RawSource.fact_count + delta)
        )
        await self._session.flush()

    async def get_facts_for_source(self, source_id: uuid.UUID) -> list[Fact]:
        """Get all facts linked to a source via FactSource, with their sources loaded."""
        stmt = (
            select(Fact)
            .join(FactSource, FactSource.fact_id == Fact.id)
            .where(FactSource.raw_source_id == source_id)
            .options(selectinload(Fact.sources).selectinload(FactSource.raw_source))
            .order_by(Fact.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().unique().all())

    async def get_fact_sources_for_source(self, source_id: uuid.UUID) -> list[FactSource]:
        """Get all FactSource junction rows for a given source."""
        stmt = select(FactSource).where(FactSource.raw_source_id == source_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_prohibited_chunks(self, source_id: uuid.UUID) -> list[ProhibitedChunk]:
        """Get all prohibited chunks for a given source."""
        stmt = (
            select(ProhibitedChunk)
            .where(ProhibitedChunk.raw_source_id == source_id)
            .order_by(ProhibitedChunk.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_linked_nodes_for_source(self, source_id: uuid.UUID) -> list[dict]:
        """Get nodes linked to a source via facts, with counts."""
        from kt_db.models import Node, NodeFact

        stmt = (
            select(
                Node.id,
                Node.concept,
                Node.node_type,
                func.count(Fact.id).label("fact_count"),
            )
            .join(NodeFact, NodeFact.node_id == Node.id)
            .join(Fact, Fact.id == NodeFact.fact_id)
            .join(FactSource, FactSource.fact_id == Fact.id)
            .where(FactSource.raw_source_id == source_id)
            .group_by(Node.id, Node.concept, Node.node_type)
            .order_by(func.count(Fact.id).desc())
        )
        result = await self._session.execute(stmt)
        return [
            {
                "node_id": str(row.id),
                "concept": row.concept,
                "node_type": row.node_type,
                "fact_count": row.fact_count,
            }
            for row in result.all()
        ]

    # ── Insights aggregate queries ─────────────────────────────────────

    async def get_insights_summary(self, since: datetime | None = None) -> dict:
        """Get aggregate counts: total, failed fetches, pending super sources."""
        stmt = select(
            func.count(RawSource.id).label("total_count"),
            func.count(
                case(
                    (
                        (RawSource.is_full_text.is_(False)) & (RawSource.fetch_attempted.is_(True)),
                        RawSource.id,
                    ),
                )
            ).label("failed_count"),
            func.count(
                case(
                    (
                        (RawSource.is_super_source.is_(True)) & (RawSource.fetch_attempted.is_(False)),
                        RawSource.id,
                    ),
                )
            ).label("pending_super_count"),
        )
        if since is not None:
            stmt = stmt.where(RawSource.retrieved_at >= since)
        row = (await self._session.execute(stmt)).one()
        return {
            "total_count": row.total_count,
            "failed_count": row.failed_count,
            "pending_super_count": row.pending_super_count,
        }

    async def get_top_failed_domains(self, since: datetime | None = None, limit: int = 15) -> list[dict]:
        """Get domains with the most fetch failures."""
        domain_expr = func.substring(RawSource.uri, text("'://([^/]+)'"))
        stmt = (
            select(
                domain_expr.label("domain"),
                func.count(RawSource.id).label("failure_count"),
            )
            .where(RawSource.is_full_text.is_(False), RawSource.fetch_attempted.is_(True))
            .group_by(domain_expr)
            .having(domain_expr.isnot(None))
            .order_by(func.count(RawSource.id).desc())
            .limit(limit)
        )
        if since is not None:
            stmt = stmt.where(RawSource.retrieved_at >= since)
        result = await self._session.execute(stmt)
        return [{"domain": row.domain, "failure_count": row.failure_count} for row in result.all()]

    async def get_common_fetch_errors(self, since: datetime | None = None, limit: int = 15) -> list[dict]:
        """Get most common fetch error messages (grouped by first 150 chars)."""
        error_group = func.left(RawSource.fetch_error, 150)
        stmt = (
            select(
                error_group.label("error_group"),
                func.count(RawSource.id).label("count"),
            )
            .where(RawSource.fetch_error.isnot(None))
            .group_by(error_group)
            .order_by(func.count(RawSource.id).desc())
            .limit(limit)
        )
        if since is not None:
            stmt = stmt.where(RawSource.retrieved_at >= since)
        result = await self._session.execute(stmt)
        return [{"error_group": row.error_group, "count": row.count} for row in result.all()]

    async def get_failures_per_day(self, since: datetime | None = None) -> list[dict]:
        """Get daily failure counts for charting."""
        day_expr = cast(RawSource.retrieved_at, Date)
        stmt = (
            select(
                day_expr.label("day"),
                func.count(RawSource.id).label("failure_count"),
            )
            .where(RawSource.is_full_text.is_(False), RawSource.fetch_attempted.is_(True))
            .group_by(day_expr)
            .order_by(day_expr.asc())
        )
        if since is not None:
            stmt = stmt.where(RawSource.retrieved_at >= since)
        result = await self._session.execute(stmt)
        return [{"day": str(row.day), "failure_count": row.failure_count} for row in result.all()]
