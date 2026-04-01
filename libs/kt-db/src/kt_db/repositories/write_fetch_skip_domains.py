"""Repository for admin-managed fetch skip domains."""

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.write_models import WriteFetchSkipDomain


class WriteFetchSkipDomainRepository:
    """CRUD for domains that should be skipped during content fetching."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[WriteFetchSkipDomain]:
        """List all skipped domains."""
        result = await self._session.execute(select(WriteFetchSkipDomain).order_by(WriteFetchSkipDomain.domain))
        return list(result.scalars().all())

    async def get_all_domains(self) -> set[str]:
        """Return set of all skipped domain strings."""
        result = await self._session.execute(select(WriteFetchSkipDomain.domain))
        return {row for row in result.scalars().all()}

    async def is_domain_skipped(self, domain: str) -> bool:
        """Check if a domain is in the skip list."""
        result = await self._session.execute(
            select(WriteFetchSkipDomain.domain).where(WriteFetchSkipDomain.domain == domain).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def add_domain(self, domain: str, reason: str) -> WriteFetchSkipDomain:
        """Add a domain to the skip list (upsert)."""
        now = datetime.now(UTC).replace(tzinfo=None)
        stmt = (
            pg_insert(WriteFetchSkipDomain)
            .values(domain=domain, reason=reason, created_at=now, updated_at=now)
            .on_conflict_do_update(
                index_elements=["domain"],
                set_={"reason": reason, "updated_at": now},
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()
        result = await self._session.execute(select(WriteFetchSkipDomain).where(WriteFetchSkipDomain.domain == domain))
        return result.scalar_one()

    async def remove_domain(self, domain: str) -> bool:
        """Remove a domain from the skip list. Returns True if removed."""
        result = await self._session.execute(delete(WriteFetchSkipDomain).where(WriteFetchSkipDomain.domain == domain))
        await self._session.flush()
        return result.rowcount > 0  # type: ignore[union-attr]
