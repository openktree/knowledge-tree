import uuid

import pytest

from kt_db.repositories.nodes import NodeRepository
from kt_db.repositories.write_nodes import WriteNodeRepository

pytestmark = pytest.mark.asyncio


async def test_create_node(db_session):
    repo = NodeRepository(db_session)
    node = await repo.create(concept="water")
    assert node.id is not None
    assert node.concept == "water"
    assert node.access_count == 0


async def test_get_by_id(db_session):
    repo = NodeRepository(db_session)
    node = await repo.create(concept="hydrogen")
    found = await repo.get_by_id(node.id)
    assert found is not None
    assert found.concept == "hydrogen"


async def test_get_by_id_not_found(db_session):
    repo = NodeRepository(db_session)
    found = await repo.get_by_id(uuid.uuid4())
    assert found is None


async def test_search_by_concept(db_session):
    repo = NodeRepository(db_session)
    await repo.create(concept="photosynthesis_node_test")
    results = await repo.search_by_concept("photosynthesis_node_test")
    assert len(results) >= 1
    assert any(n.concept == "photosynthesis_node_test" for n in results)


async def test_search_by_concept_case_insensitive(db_session):
    repo = NodeRepository(db_session)
    await repo.create(concept="QuantumMechanics_node_test")
    results = await repo.search_by_concept("quantummechanics_node_test")
    assert len(results) >= 1


async def test_search_by_concept_exact_match_first(db_session):
    """Exact concept match should rank above longer compound concepts."""
    repo = NodeRepository(db_session)
    # Create a compound concept first, then the exact match
    await repo.create(concept="electricity in the body")
    await repo.create(concept="electricity")
    await repo.create(concept="electricity and magnetism")

    results = await repo.search_by_concept("electricity")
    assert len(results) >= 2
    # The exact match "electricity" must be first
    assert results[0].concept == "electricity"


async def test_search_by_trigram_exact_match_first(db_session):
    """search_by_trigram also ranks exact matches first."""
    repo = NodeRepository(db_session)
    await repo.create(concept="gravity in quantum mechanics")
    await repo.create(concept="gravity")
    await repo.create(concept="gravity waves")

    results = await repo.search_by_trigram("gravity", threshold=0.2)
    assert len(results) >= 2
    assert results[0].concept == "gravity"


async def test_increment_access_count(db_session):
    repo = NodeRepository(db_session)
    node = await repo.create(concept="counted_node_test")

    # Counters live in node_counters table, not on the node row
    access, _update = await repo.get_counters(node.id)
    assert access == 0

    await repo.increment_access_count(node.id)
    access, _update = await repo.get_counters(node.id)
    assert access == 1

    await repo.increment_access_count(node.id)
    access, _update = await repo.get_counters(node.id)
    assert access == 2


async def test_create_with_all_fields(db_session):
    repo = NodeRepository(db_session)
    node = await repo.create(
        concept="full_node_test",
        attractor="science",
        filter_id="physics",
        max_content_tokens=1000,
    )
    assert node.attractor == "science"
    assert node.filter_id == "physics"
    assert node.max_content_tokens == 1000


async def test_update_fields(db_session):
    repo = NodeRepository(db_session)
    node = await repo.create(concept="update_fields_test")
    await repo.update_fields(node.id, concept="updated_concept_test")
    await db_session.refresh(node)
    assert node.concept == "updated_concept_test"


async def test_list_paginated(db_session):
    repo = NodeRepository(db_session)
    # Create several nodes with unique prefix
    for i in range(5):
        await repo.create(concept=f"paginate_test_{i}")
    results = await repo.list_paginated(offset=0, limit=3, search="paginate_test_")
    assert len(results) <= 3
    assert all("paginate_test_" in n.concept for n in results)


async def test_list_paginated_with_search(db_session):
    repo = NodeRepository(db_session)
    await repo.create(concept="unique_xyz_listtest")
    results = await repo.list_paginated(offset=0, limit=10, search="unique_xyz_listtest")
    assert len(results) >= 1
    assert results[0].concept == "unique_xyz_listtest"


async def test_count(db_session):
    repo = NodeRepository(db_session)
    await repo.create(concept="count_test_alpha")
    total = await repo.count(search="count_test_alpha")
    assert total >= 1


async def test_delete_node(db_session):
    repo = NodeRepository(db_session)
    node = await repo.create(concept="delete_me_node")
    assert await repo.delete(node.id) is True
    assert await repo.get_by_id(node.id) is None
    # Deleting again returns False
    assert await repo.delete(node.id) is False


async def test_write_node_search_by_trigram_exact_match_first(write_db_session):
    """Write-db search_by_trigram also ranks exact matches first."""
    repo = WriteNodeRepository(write_db_session)
    await repo.upsert("concept", "magnetism in biology")
    await repo.upsert("concept", "magnetism")
    await repo.upsert("concept", "magnetism and electricity")
    await write_db_session.flush()

    results = await repo.search_by_trigram("magnetism", threshold=0.2)
    assert len(results) >= 2
    assert results[0].concept == "magnetism"
