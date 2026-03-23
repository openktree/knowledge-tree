import uuid

import pytest

from kt_db.models import Node
from kt_db.repositories.facts import FactRepository

pytestmark = pytest.mark.asyncio


async def test_create_and_get_fact(db_session):
    repo = FactRepository(db_session)
    fact = await repo.create(content="Water boils at 100C", fact_type="measurement")
    assert fact.id is not None
    assert fact.content == "Water boils at 100C"
    assert fact.fact_type == "measurement"

    found = await repo.get_by_id(fact.id)
    assert found is not None
    assert found.content == "Water boils at 100C"


async def test_get_by_id_not_found(db_session):
    repo = FactRepository(db_session)
    found = await repo.get_by_id(uuid.uuid4())
    assert found is None


async def test_create_with_metadata(db_session):
    repo = FactRepository(db_session)
    fact = await repo.create(
        content="The Earth orbits the Sun",
        fact_type="claim",
        metadata={"source": "astronomy"},
    )
    assert fact.metadata_ == {"source": "astronomy"}


async def test_get_facts_by_type(db_session):
    repo = FactRepository(db_session)
    await repo.create(content="Fact type test: formula", fact_type="formula")
    await repo.create(content="Another formula", fact_type="formula")

    results = await repo.get_facts_by_type("formula")
    assert len(results) >= 2
    assert all(f.fact_type == "formula" for f in results)


async def test_link_to_node(db_session):
    repo = FactRepository(db_session)
    # Create a node
    node = Node(id=uuid.uuid4(), concept="test_link_concept")
    db_session.add(node)
    await db_session.flush()

    # Create a fact
    fact = await repo.create(content="Linked fact", fact_type="claim")

    # Link them
    node_fact = await repo.link_to_node(node.id, fact.id, relevance_score=0.9)
    assert node_fact.node_id == node.id
    assert node_fact.fact_id == fact.id
    assert node_fact.relevance_score == 0.9


async def test_get_facts_by_node(db_session):
    repo = FactRepository(db_session)
    # Create a node
    node = Node(id=uuid.uuid4(), concept="test_get_facts_by_node_concept")
    db_session.add(node)
    await db_session.flush()

    # Create and link facts
    fact1 = await repo.create(content="Node fact 1", fact_type="claim")
    fact2 = await repo.create(content="Node fact 2", fact_type="account")
    await repo.link_to_node(node.id, fact1.id)
    await repo.link_to_node(node.id, fact2.id)

    # Retrieve
    facts = await repo.get_facts_by_node(node.id)
    assert len(facts) == 2
    contents = {f.content for f in facts}
    assert "Node fact 1" in contents
    assert "Node fact 2" in contents


async def test_list_paginated(db_session):
    repo = FactRepository(db_session)
    for i in range(5):
        await repo.create(content=f"paginate_fact_test_{i}", fact_type="claim")
    results = await repo.list_paginated(offset=0, limit=3, search="paginate_fact_test_")
    assert len(results) <= 3
    assert all("paginate_fact_test_" in f.content for f in results)


async def test_list_paginated_with_type_filter(db_session):
    repo = FactRepository(db_session)
    await repo.create(content="type_filter_test_fact", fact_type="statistical")
    results = await repo.list_paginated(offset=0, limit=10, fact_type="statistical", search="type_filter_test_fact")
    assert len(results) >= 1
    assert all(f.fact_type == "statistical" for f in results)


async def test_count(db_session):
    repo = FactRepository(db_session)
    await repo.create(content="count_fact_test_alpha", fact_type="claim")
    total = await repo.count(search="count_fact_test_alpha")
    assert total >= 1


async def test_update_fact_fields(db_session):
    repo = FactRepository(db_session)
    fact = await repo.create(content="original content", fact_type="claim")
    await repo.update_fields(fact.id, content="updated content", fact_type="account")
    await db_session.refresh(fact)
    assert fact.content == "updated content"
    assert fact.fact_type == "account"


async def test_delete_fact(db_session):
    repo = FactRepository(db_session)
    fact = await repo.create(content="delete_me_fact", fact_type="claim")
    assert await repo.delete(fact.id) is True
    assert await repo.get_by_id(fact.id) is None
    assert await repo.delete(fact.id) is False


async def test_get_facts_by_node_with_sources(db_session):
    from kt_config.types import RawSearchResult
    from kt_db.repositories.sources import SourceRepository

    repo = FactRepository(db_session)

    # Create a node
    node = Node(id=uuid.uuid4(), concept="test_with_sources_concept")
    db_session.add(node)
    await db_session.flush()

    # Create a fact and link to node
    fact = await repo.create(content="Fact with sources for node", fact_type="claim")
    await repo.link_to_node(node.id, fact.id)

    # Create a raw source and link to fact
    source_repo = SourceRepository(db_session)
    search_result = RawSearchResult(
        uri="https://example.com/with-sources-test",
        title="Source Title",
        raw_content="Content for with-sources test unique-" + str(uuid.uuid4()),
        provider_id="test",
    )
    raw_source, _ = await source_repo.create_or_get(search_result)
    await repo.create_fact_source(
        fact_id=fact.id,
        raw_source_id=raw_source.id,
        context_snippet="test snippet",
        attribution="test author",
    )

    # Fetch with sources
    facts = await repo.get_facts_by_node_with_sources(node.id)
    assert len(facts) == 1
    assert facts[0].content == "Fact with sources for node"
    assert len(facts[0].sources) == 1
    assert facts[0].sources[0].raw_source.uri == "https://example.com/with-sources-test"
    assert facts[0].sources[0].context_snippet == "test snippet"
    assert facts[0].sources[0].attribution == "test author"


async def test_get_by_id_with_sources(db_session):
    from kt_config.types import RawSearchResult
    from kt_db.repositories.sources import SourceRepository

    repo = FactRepository(db_session)

    fact = await repo.create(content="Fact for get_by_id_with_sources", fact_type="claim")

    source_repo = SourceRepository(db_session)
    search_result = RawSearchResult(
        uri="https://example.com/get-by-id-sources",
        title="Get By ID Source",
        raw_content="Content unique-" + str(uuid.uuid4()),
        provider_id="test",
    )
    raw_source, _ = await source_repo.create_or_get(search_result)
    await repo.create_fact_source(fact_id=fact.id, raw_source_id=raw_source.id)

    result = await repo.get_by_id_with_sources(fact.id)
    assert result is not None
    assert result.content == "Fact for get_by_id_with_sources"
    assert len(result.sources) == 1
    assert result.sources[0].raw_source.uri == "https://example.com/get-by-id-sources"


async def test_get_by_id_with_sources_not_found(db_session):
    repo = FactRepository(db_session)
    result = await repo.get_by_id_with_sources(uuid.uuid4())
    assert result is None


async def test_create_fact_source(db_session):
    from kt_config.types import RawSearchResult
    from kt_db.repositories.sources import SourceRepository

    # Create a raw source
    source_repo = SourceRepository(db_session)
    search_result = RawSearchResult(
        uri="https://example.com/fact-source-test",
        title="Test Source",
        raw_content="Content for fact source test unique-hash-" + str(uuid.uuid4()),
        provider_id="test",
    )
    raw_source, _ = await source_repo.create_or_get(search_result)

    # Create a fact
    repo = FactRepository(db_session)
    fact = await repo.create(content="Fact with source", fact_type="claim")

    # Create fact-source link
    fact_source = await repo.create_fact_source(
        fact_id=fact.id,
        raw_source_id=raw_source.id,
        context_snippet="snippet text",
        attribution="who: Author; where: Publication",
    )
    assert fact_source is not None
    assert fact_source.fact_id == fact.id
    assert fact_source.raw_source_id == raw_source.id
    assert fact_source.context_snippet == "snippet text"
    assert fact_source.attribution == "who: Author; where: Publication"


async def test_create_fact_source_duplicate_is_idempotent(db_session):
    from kt_config.types import RawSearchResult
    from kt_db.repositories.sources import SourceRepository

    source_repo = SourceRepository(db_session)
    search_result = RawSearchResult(
        uri="https://example.com/dedup-test",
        title="Dedup Source",
        raw_content="Content for dedup test unique-hash-" + str(uuid.uuid4()),
        provider_id="test",
    )
    raw_source, _ = await source_repo.create_or_get(search_result)

    repo = FactRepository(db_session)
    fact = await repo.create(content="Dedup fact source test", fact_type="claim")

    # First link succeeds
    first = await repo.create_fact_source(
        fact_id=fact.id,
        raw_source_id=raw_source.id,
        context_snippet="first snippet",
    )
    assert first is not None

    # Second link with same (fact_id, raw_source_id) returns None
    second = await repo.create_fact_source(
        fact_id=fact.id,
        raw_source_id=raw_source.id,
        context_snippet="second snippet",
    )
    assert second is None
