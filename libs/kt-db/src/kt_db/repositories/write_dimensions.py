"""Write-optimized dimension repository."""

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.keys import make_dimension_key
from kt_db.write_models import WriteConvergenceReport, WriteDimension, WriteDivergentClaim


class WriteDimensionRepository:
    """Upsert-only repository for dimensions in the write-optimized database."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        node_key: str,
        model_id: str,
        content: str,
        *,
        confidence: float = 0.0,
        suggested_concepts: list[str] | None = None,
        batch_index: int = 0,
        fact_count: int = 0,
        is_definitive: bool = False,
        fact_ids: list[str] | None = None,
        metadata_: dict | None = None,
    ) -> str:
        """Insert or update a dimension. Returns the deterministic key."""
        key = make_dimension_key(node_key, model_id, batch_index)

        stmt = (
            pg_insert(WriteDimension)
            .values(
                key=key,
                node_key=node_key,
                model_id=model_id,
                content=content,
                confidence=confidence,
                suggested_concepts=suggested_concepts,
                batch_index=batch_index,
                fact_count=fact_count,
                is_definitive=is_definitive,
                fact_ids=fact_ids,
                metadata_=metadata_,
            )
            .on_conflict_do_update(
                index_elements=[WriteDimension.key],
                set_={
                    "content": content,
                    "confidence": confidence,
                    "suggested_concepts": suggested_concepts,
                    "fact_count": fact_count,
                    "is_definitive": is_definitive,
                    "fact_ids": fact_ids,
                    "metadata": metadata_,
                    "updated_at": func.clock_timestamp(),
                },
            )
        )
        await self._session.execute(stmt)
        return key

    async def upsert_convergence_report(
        self,
        node_key: str,
        convergence_score: float,
        *,
        converged_claims: list[str] | None = None,
        recommended_content: str | None = None,
    ) -> str:
        """Insert or update a convergence report for a node."""
        stmt = (
            pg_insert(WriteConvergenceReport)
            .values(
                node_key=node_key,
                convergence_score=convergence_score,
                converged_claims=converged_claims,
                recommended_content=recommended_content,
            )
            .on_conflict_do_update(
                index_elements=[WriteConvergenceReport.node_key],
                set_={
                    "convergence_score": convergence_score,
                    "converged_claims": converged_claims,
                    "recommended_content": recommended_content,
                    "updated_at": func.clock_timestamp(),
                },
            )
        )
        await self._session.execute(stmt)
        return node_key

    async def delete_by_key(self, dim_key: str) -> bool:
        """Delete a dimension by key. Returns True if deleted."""
        from sqlalchemy import delete as sa_delete

        result = await self._session.execute(
            sa_delete(WriteDimension).where(WriteDimension.key == dim_key)
        )
        return (result.rowcount or 0) > 0

    async def delete_convergence_report(self, node_key: str) -> bool:
        """Delete convergence report for a node. Returns True if deleted."""
        from sqlalchemy import delete as sa_delete

        result = await self._session.execute(
            sa_delete(WriteConvergenceReport).where(WriteConvergenceReport.node_key == node_key)
        )
        return (result.rowcount or 0) > 0

    async def delete_divergent_claims(self, node_key: str) -> int:
        """Delete all divergent claims for a node. Returns count deleted."""
        from sqlalchemy import delete as sa_delete

        result = await self._session.execute(
            sa_delete(WriteDivergentClaim).where(WriteDivergentClaim.node_key == node_key)
        )
        return result.rowcount or 0

    async def get_by_node_key(self, node_key: str, limit: int = 10) -> list[WriteDimension]:
        """Return dimensions for a node by its write-db key."""
        stmt = (
            select(WriteDimension)
            .where(WriteDimension.node_key == node_key)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
