"""Tests for WriteDimensionRepository."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.repositories.write_dimensions import WriteDimensionRepository


@pytest.mark.asyncio
class TestWriteDimensionRepository:
    """Tests for dimension CRUD operations."""

    async def _create_dim(
        self,
        repo: WriteDimensionRepository,
        node_key: str,
        batch_index: int,
        is_definitive: bool,
    ) -> str:
        return await repo.upsert(
            node_key=node_key,
            model_id="test-model",
            content=f"dimension content batch {batch_index}",
            batch_index=batch_index,
            fact_count=60 if is_definitive else 10,
            is_definitive=is_definitive,
        )

    async def test_delete_drafts_keeps_definitive(self, write_db_session: AsyncSession) -> None:
        repo = WriteDimensionRepository(write_db_session)
        node_key = "concept:test-delete-drafts"

        # Create a mix of definitive and draft dimensions
        await self._create_dim(repo, node_key, batch_index=0, is_definitive=True)
        await self._create_dim(repo, node_key, batch_index=1, is_definitive=True)
        await self._create_dim(repo, node_key, batch_index=2, is_definitive=False)
        await self._create_dim(repo, node_key, batch_index=3, is_definitive=False)
        await write_db_session.flush()

        deleted = await repo.delete_drafts_for_node(node_key)
        await write_db_session.flush()

        assert deleted == 2

        remaining = await repo.get_by_node_key(node_key)
        assert len(remaining) == 2
        assert all(d.is_definitive for d in remaining)
        assert {d.batch_index for d in remaining} == {0, 1}

    async def test_delete_drafts_no_drafts(self, write_db_session: AsyncSession) -> None:
        repo = WriteDimensionRepository(write_db_session)
        node_key = "concept:test-no-drafts"

        await self._create_dim(repo, node_key, batch_index=0, is_definitive=True)
        await write_db_session.flush()

        deleted = await repo.delete_drafts_for_node(node_key)

        assert deleted == 0
        remaining = await repo.get_by_node_key(node_key)
        assert len(remaining) == 1

    async def test_delete_drafts_all_drafts(self, write_db_session: AsyncSession) -> None:
        repo = WriteDimensionRepository(write_db_session)
        node_key = "concept:test-all-drafts"

        await self._create_dim(repo, node_key, batch_index=0, is_definitive=False)
        await self._create_dim(repo, node_key, batch_index=1, is_definitive=False)
        await write_db_session.flush()

        deleted = await repo.delete_drafts_for_node(node_key)
        await write_db_session.flush()

        assert deleted == 2
        remaining = await repo.get_by_node_key(node_key)
        assert len(remaining) == 0

    async def test_delete_drafts_does_not_affect_other_nodes(self, write_db_session: AsyncSession) -> None:
        repo = WriteDimensionRepository(write_db_session)
        node_a = "concept:test-node-a"
        node_b = "concept:test-node-b"

        await self._create_dim(repo, node_a, batch_index=0, is_definitive=False)
        await self._create_dim(repo, node_b, batch_index=0, is_definitive=False)
        await write_db_session.flush()

        deleted = await repo.delete_drafts_for_node(node_a)
        await write_db_session.flush()

        assert deleted == 1
        remaining_b = await repo.get_by_node_key(node_b)
        assert len(remaining_b) == 1
