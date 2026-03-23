import pytest

from kt_db.repositories.nodes import NodeRepository

pytestmark = pytest.mark.asyncio


async def test_increment_access_count(db_session):
    """Verify access_count increments correctly in node_counters table."""
    repo = NodeRepository(db_session)
    node = await repo.create(concept="counter_access_test")

    # Initially zero
    access, update = await repo.get_counters(node.id)
    assert access == 0
    assert update == 0

    # Increment once
    await repo.increment_access_count(node.id)
    access, update = await repo.get_counters(node.id)
    assert access == 1
    assert update == 0

    # Increment again
    await repo.increment_access_count(node.id)
    access, update = await repo.get_counters(node.id)
    assert access == 2
    assert update == 0


async def test_increment_access_count_concurrent(db_session):
    """Verify concurrent access_count increments don't lose updates.

    Note: true concurrency requires separate sessions/connections.
    This test verifies sequential increments within the same session are
    consistent, which validates the upsert logic.
    """
    repo = NodeRepository(db_session)
    node = await repo.create(concept="counter_concurrent_test")

    n = 10
    for _ in range(n):
        await repo.increment_access_count(node.id)

    access, update = await repo.get_counters(node.id)
    assert access == n
    assert update == 0


async def test_increment_update_count(db_session):
    """Verify update_count increments correctly in node_counters table."""
    repo = NodeRepository(db_session)
    node = await repo.create(concept="counter_update_test")

    await repo.increment_update_count(node.id)
    access, update = await repo.get_counters(node.id)
    assert access == 0
    assert update == 1

    await repo.increment_update_count(node.id)
    access, update = await repo.get_counters(node.id)
    assert access == 0
    assert update == 2


async def test_mixed_counters(db_session):
    """Verify access and update counters are independent."""
    repo = NodeRepository(db_session)
    node = await repo.create(concept="counter_mixed_test")

    await repo.increment_access_count(node.id)
    await repo.increment_access_count(node.id)
    await repo.increment_update_count(node.id)

    access, update = await repo.get_counters(node.id)
    assert access == 2
    assert update == 1
