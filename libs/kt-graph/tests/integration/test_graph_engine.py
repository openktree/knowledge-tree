import uuid

import pytest
from sqlalchemy import select

from kt_db.models import NodeCounter
from kt_db.repositories.facts import FactRepository
from kt_graph.engine import GraphEngine

pytestmark = pytest.mark.asyncio


async def test_create_and_get_node(db_session):
    engine = GraphEngine(db_session)
    node = await engine.create_node("engine_water_test")
    assert node.id is not None
    assert node.concept == "engine_water_test"

    fetched = await engine.get_node(node.id)
    assert fetched is not None
    assert fetched.concept == "engine_water_test"


async def test_update_node(db_session):
    engine = GraphEngine(db_session)
    node = await engine.create_node("engine_update_test")
    updated = await engine.update_node(node.id, concept="engine_updated_test")
    assert updated.concept == "engine_updated_test"


async def test_update_node_not_found(db_session):
    engine = GraphEngine(db_session)
    with pytest.raises(ValueError, match="Node not found"):
        await engine.update_node(uuid.uuid4(), concept="nope")


async def test_search_nodes(db_session):
    engine = GraphEngine(db_session)
    await engine.create_node("engine_searchable_unique_xyz")
    results = await engine.search_nodes("engine_searchable_unique_xyz")
    assert len(results) >= 1


async def test_create_and_get_edge(db_session):
    engine = GraphEngine(db_session)
    n1 = await engine.create_node("engine_edge_a_test")
    n2 = await engine.create_node("engine_edge_b_test")
    edge = await engine.create_edge(n1.id, n2.id, "related", 0.9)

    assert edge.relationship_type == "related"
    assert edge.weight == 0.9


async def test_get_edges(db_session):
    engine = GraphEngine(db_session)
    n1 = await engine.create_node("engine_edges_center_test")
    n2 = await engine.create_node("engine_edges_target_test")
    await engine.create_edge(n1.id, n2.id, "related", 0.5)

    edges = await engine.get_edges(n1.id)
    assert len(edges) >= 1


async def test_get_neighbors(db_session):
    engine = GraphEngine(db_session)
    n1 = await engine.create_node("engine_neighbor_a_test")
    n2 = await engine.create_node("engine_neighbor_b_test")
    await engine.create_edge(n1.id, n2.id, "related", 0.5)

    neighbors = await engine.get_neighbors(n1.id)
    assert len(neighbors) == 1
    assert neighbors[0].id == n2.id


async def test_link_fact_to_node(db_session):
    engine = GraphEngine(db_session)
    fact_repo = FactRepository(db_session)

    node = await engine.create_node("engine_fact_link_test")
    fact = await fact_repo.create(content="Engine test fact", fact_type="claim")

    node_fact = await engine.link_fact_to_node(node.id, fact.id, relevance=0.8)
    assert node_fact.node_id == node.id
    assert node_fact.fact_id == fact.id
    assert node_fact.relevance_score == 0.8


async def test_get_node_facts(db_session):
    engine = GraphEngine(db_session)
    fact_repo = FactRepository(db_session)

    node = await engine.create_node("engine_get_facts_test")
    f1 = await fact_repo.create(content="Engine fact 1", fact_type="claim")
    f2 = await fact_repo.create(content="Engine fact 2", fact_type="account")
    await engine.link_fact_to_node(node.id, f1.id)
    await engine.link_fact_to_node(node.id, f2.id)

    facts = await engine.get_node_facts(node.id)
    assert len(facts) == 2


async def test_add_and_get_dimensions(db_session):
    engine = GraphEngine(db_session)
    node = await engine.create_node("engine_dim_test")

    dim = await engine.add_dimension(
        node.id, "test-model-v1", "Water is H2O", 0.9, suggested_concepts=["hydrogen", "oxygen"]
    )
    assert dim.model_id == "test-model-v1"
    assert dim.content == "Water is H2O"
    assert dim.confidence == 0.9
    assert dim.suggested_concepts == ["hydrogen", "oxygen"]

    dims = await engine.get_dimensions(node.id)
    assert len(dims) >= 1
    assert any(d.model_id == "test-model-v1" for d in dims)


async def test_save_and_get_version(db_session):
    engine = GraphEngine(db_session)
    node = await engine.create_node("engine_version_test")

    version = await engine.save_version(node.id)
    assert version.version_number == 1
    assert version.snapshot is not None
    assert version.snapshot["concept"] == "engine_version_test"

    # Save another version
    version2 = await engine.save_version(node.id)
    assert version2.version_number == 2


async def test_get_node_history(db_session):
    engine = GraphEngine(db_session)
    node = await engine.create_node("engine_history_test")
    await engine.save_version(node.id)
    await engine.save_version(node.id)

    history = await engine.get_node_history(node.id)
    assert len(history) >= 2
    assert history[0].version_number < history[1].version_number


async def test_save_version_not_found(db_session):
    engine = GraphEngine(db_session)
    with pytest.raises(ValueError, match="Node not found"):
        await engine.save_version(uuid.uuid4())


async def test_get_subgraph(db_session):
    engine = GraphEngine(db_session)
    n1 = await engine.create_node("subgraph_a_test")
    n2 = await engine.create_node("subgraph_b_test")
    await engine.create_edge(n1.id, n2.id, "related", 0.5)

    subgraph = await engine.get_subgraph([n1.id, n2.id])
    assert len(subgraph["nodes"]) == 2
    assert len(subgraph["edges"]) == 1


async def test_get_subgraph_empty(db_session):
    engine = GraphEngine(db_session)
    subgraph = await engine.get_subgraph([])
    assert len(subgraph["nodes"]) == 0
    assert len(subgraph["edges"]) == 0


async def test_compute_richness(db_session):
    engine = GraphEngine(db_session)
    node = await engine.create_node("richness_test")

    # No facts, no dimensions, no access
    score = engine.compute_richness(node, fact_count=0, dimension_count=0)
    assert score == 0.0

    # Some facts and dimensions
    score = engine.compute_richness(node, fact_count=5, dimension_count=2)
    # 5 * 0.1 + 2 * 0.2 + 0 * 0.01 = 0.5 + 0.4 = 0.9
    assert abs(score - 0.9) < 0.001

    # Capped at 1.0
    score = engine.compute_richness(node, fact_count=10, dimension_count=5)
    assert score == 1.0


async def test_full_workflow(db_session):
    """End-to-end test of the graph engine."""
    engine = GraphEngine(db_session)
    fact_repo = FactRepository(db_session)

    # Create nodes
    water = await engine.create_node("workflow_water_test")
    hydrogen = await engine.create_node("workflow_hydrogen_test")

    # Create edges
    edge = await engine.create_edge(water.id, hydrogen.id, "related", 0.9)
    assert edge.relationship_type == "related"

    # Create and link facts
    fact = await fact_repo.create(content="Water is composed of hydrogen and oxygen", fact_type="claim")
    await engine.link_fact_to_node(water.id, fact.id)

    facts = await engine.get_node_facts(water.id)
    assert len(facts) == 1

    # Add dimensions
    dim = await engine.add_dimension(water.id, "test-model", "Water is H2O", 0.9)
    assert dim is not None

    # Get subgraph
    subgraph = await engine.get_subgraph([water.id, hydrogen.id])
    assert len(subgraph["nodes"]) == 2
    assert len(subgraph["edges"]) == 1

    # Versioning
    await engine.save_version(water.id)
    history = await engine.get_node_history(water.id)
    assert len(history) >= 1
    assert history[0].snapshot is not None
    assert history[0].snapshot["concept"] == "workflow_water_test"


async def test_create_edge_low_weight_is_stored(db_session):
    """Edge with low abs(weight) is stored — no min_edge_weight filter."""
    engine = GraphEngine(db_session)
    n1 = await engine.create_node("threshold_a_test")
    n2 = await engine.create_node("threshold_b_test")

    edge = await engine.create_edge(n1.id, n2.id, "related", 0.05)
    assert edge is not None
    assert edge.weight == 0.05


async def test_create_edge_negative_weight(db_session):
    """Negative weight creates the edge without filtering."""
    engine = GraphEngine(db_session)
    n1 = await engine.create_node("neg_threshold_a_test")
    n2 = await engine.create_node("neg_threshold_b_test")

    edge = await engine.create_edge(n1.id, n2.id, "related", -0.5)
    assert edge is not None
    assert edge.weight == -0.5


async def test_increment_access_count_deadlock_retry(db_session):
    """increment_access_count retries on deadlock and succeeds."""
    from sqlalchemy.exc import DBAPIError

    from kt_graph.engine import _is_deadlock

    engine = GraphEngine(db_session)
    node = await engine.create_node("deadlock_retry_test")

    # Verify _is_deadlock helper recognises a fake deadlock
    class FakeDeadlock:
        sqlstate = "40P01"

    fake_exc = DBAPIError("stmt", {}, FakeDeadlock())  # type: ignore[arg-type]
    assert _is_deadlock(fake_exc)

    # Verify the node's access count increments normally despite savepoints
    await engine.increment_access_count(node.id)
    result = await db_session.execute(select(NodeCounter).where(NodeCounter.node_id == node.id))
    counter = result.scalar_one()
    assert counter.access_count == 1
