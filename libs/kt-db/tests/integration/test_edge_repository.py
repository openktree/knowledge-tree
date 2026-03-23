import pytest

from kt_db.repositories.edges import EdgeRepository
from kt_db.repositories.nodes import NodeRepository

pytestmark = pytest.mark.asyncio


async def test_create_edge(db_session):
    node_repo = NodeRepository(db_session)
    edge_repo = EdgeRepository(db_session)
    n1 = await node_repo.create(concept="edge_water_test")
    n2 = await node_repo.create(concept="edge_hydrogen_test")
    edge = await edge_repo.create(n1.id, n2.id, "related", 0.9)
    assert edge.relationship_type == "related"
    assert edge.weight == 0.9
    # Canonical ordering: smaller UUID is source
    expected_src = min(n1.id, n2.id)
    expected_tgt = max(n1.id, n2.id)
    assert edge.source_node_id == expected_src
    assert edge.target_node_id == expected_tgt


async def test_get_edge(db_session):
    node_repo = NodeRepository(db_session)
    edge_repo = EdgeRepository(db_session)
    n1 = await node_repo.create(concept="get_edge_a_test")
    n2 = await node_repo.create(concept="get_edge_b_test")
    await edge_repo.create(n1.id, n2.id, "related", 0.5)

    found = await edge_repo.get_edge(n1.id, n2.id, "related")
    assert found is not None
    assert found.relationship_type == "related"


async def test_get_edge_not_found(db_session):
    node_repo = NodeRepository(db_session)
    edge_repo = EdgeRepository(db_session)
    n1 = await node_repo.create(concept="no_edge_a_test")
    n2 = await node_repo.create(concept="no_edge_b_test")

    found = await edge_repo.get_edge(n1.id, n2.id, "related")
    assert found is None


async def test_upsert_edge(db_session):
    node_repo = NodeRepository(db_session)
    edge_repo = EdgeRepository(db_session)
    n1 = await node_repo.create(concept="upsert_a_test")
    n2 = await node_repo.create(concept="upsert_b_test")

    edge1 = await edge_repo.create(n1.id, n2.id, "related", 0.5)
    edge2 = await edge_repo.create(n1.id, n2.id, "related", 0.9)

    # Should be the same edge, updated weight
    assert edge1.id == edge2.id
    assert edge2.weight == 0.9


async def test_canonical_ordering(db_session):
    """Creating A→B and B→A for the undirected 'related' type produces the same edge."""
    node_repo = NodeRepository(db_session)
    edge_repo = EdgeRepository(db_session)
    n1 = await node_repo.create(concept="canon_a_test")
    n2 = await node_repo.create(concept="canon_b_test")

    edge1 = await edge_repo.create(n1.id, n2.id, "related", 0.5)
    edge2 = await edge_repo.create(n2.id, n1.id, "related", 0.9)

    # Same edge, updated weight
    assert edge1.id == edge2.id
    assert edge2.weight == 0.9

    # Stored in canonical order (smaller UUID = source)
    expected_src = min(n1.id, n2.id)
    assert edge2.source_node_id == expected_src


async def test_get_edge_either_direction(db_session):
    """get_edge finds an undirected edge regardless of argument order."""
    node_repo = NodeRepository(db_session)
    edge_repo = EdgeRepository(db_session)
    n1 = await node_repo.create(concept="getdir_a_test")
    n2 = await node_repo.create(concept="getdir_b_test")

    edge = await edge_repo.create(n1.id, n2.id, "related", 0.7)

    found_fwd = await edge_repo.get_edge(n1.id, n2.id, "related")
    found_rev = await edge_repo.get_edge(n2.id, n1.id, "related")
    assert found_fwd is not None
    assert found_rev is not None
    assert found_fwd.id == edge.id
    assert found_rev.id == edge.id


async def test_negative_weight(db_session):
    """Edges can have negative weight (opposing relationship)."""
    node_repo = NodeRepository(db_session)
    edge_repo = EdgeRepository(db_session)
    n1 = await node_repo.create(concept="neg_weight_a_test")
    n2 = await node_repo.create(concept="neg_weight_b_test")

    edge = await edge_repo.create(n1.id, n2.id, "related", -0.7)
    assert edge.weight == -0.7


async def test_get_edges_outgoing(db_session):
    node_repo = NodeRepository(db_session)
    edge_repo = EdgeRepository(db_session)
    n1 = await node_repo.create(concept="outgoing_center_test")
    n2 = await node_repo.create(concept="outgoing_target1_test")
    n3 = await node_repo.create(concept="outgoing_target2_test")

    await edge_repo.create(n1.id, n2.id, "related", 0.8)
    await edge_repo.create(n1.id, n3.id, "related", 0.5)

    edges = await edge_repo.get_edges(n1.id, direction="both")
    # Edges are undirected with canonical UUID ordering, so direction
    # depends on UUID sort order.  "both" is the correct way to query.
    assert len(edges) == 2


async def test_get_edges_both(db_session):
    node_repo = NodeRepository(db_session)
    edge_repo = EdgeRepository(db_session)
    n1 = await node_repo.create(concept="both_center_test")
    n2 = await node_repo.create(concept="both_other_test")
    n3 = await node_repo.create(concept="both_third_test")

    await edge_repo.create(n1.id, n2.id, "related", 0.5)
    await edge_repo.create(n1.id, n3.id, "related", 0.8)

    both = await edge_repo.get_edges(n1.id, direction="both")
    assert len(both) == 2


async def test_get_edges_with_type_filter(db_session):
    node_repo = NodeRepository(db_session)
    edge_repo = EdgeRepository(db_session)
    n1 = await node_repo.create(concept="type_filter_center_test")
    n2 = await node_repo.create(concept="type_filter_target_test")

    await edge_repo.create(n1.id, n2.id, "related", 0.8)

    filtered = await edge_repo.get_edges(n1.id, direction="both", types=["related"])
    assert len(filtered) == 1
    assert filtered[0].relationship_type == "related"


async def test_get_neighbors_depth_1(db_session):
    node_repo = NodeRepository(db_session)
    edge_repo = EdgeRepository(db_session)
    center = await node_repo.create(concept="neighbor_center_test")
    leaf1 = await node_repo.create(concept="neighbor_leaf1_test")
    leaf2 = await node_repo.create(concept="neighbor_leaf2_test")

    await edge_repo.create(center.id, leaf1.id, "related", 0.5)
    await edge_repo.create(center.id, leaf2.id, "related", 0.7)

    neighbors = await edge_repo.get_neighbors(center.id, depth=1)
    assert len(neighbors) == 2
    neighbor_ids = {n.id for n in neighbors}
    assert leaf1.id in neighbor_ids
    assert leaf2.id in neighbor_ids


async def test_get_neighbors_depth_2(db_session):
    node_repo = NodeRepository(db_session)
    edge_repo = EdgeRepository(db_session)
    a = await node_repo.create(concept="depth2_a_test")
    b = await node_repo.create(concept="depth2_b_test")
    c = await node_repo.create(concept="depth2_c_test")

    await edge_repo.create(a.id, b.id, "related", 0.5)
    await edge_repo.create(b.id, c.id, "related", 0.5)

    # Depth 1: only B
    neighbors_1 = await edge_repo.get_neighbors(a.id, depth=1)
    assert len(neighbors_1) == 1
    assert neighbors_1[0].id == b.id

    # Depth 2: B and C
    neighbors_2 = await edge_repo.get_neighbors(a.id, depth=2)
    assert len(neighbors_2) == 2
    ids = {n.id for n in neighbors_2}
    assert b.id in ids
    assert c.id in ids


async def test_get_neighbors_no_edges(db_session):
    node_repo = NodeRepository(db_session)
    edge_repo = EdgeRepository(db_session)
    lonely = await node_repo.create(concept="lonely_node_test")

    neighbors = await edge_repo.get_neighbors(lonely.id, depth=1)
    assert len(neighbors) == 0


async def test_delete_edge(db_session):
    node_repo = NodeRepository(db_session)
    edge_repo = EdgeRepository(db_session)
    n1 = await node_repo.create(concept="del_edge_a_test")
    n2 = await node_repo.create(concept="del_edge_b_test")

    edge = await edge_repo.create(n1.id, n2.id, "related", 0.8)
    assert await edge_repo.get_edge(n1.id, n2.id, "related") is not None

    deleted = await edge_repo.delete(edge.id)
    assert deleted is True
    assert await edge_repo.get_edge(n1.id, n2.id, "related") is None

    # Deleting again returns False
    deleted_again = await edge_repo.delete(edge.id)
    assert deleted_again is False


async def test_create_edge_with_justification(db_session):
    """Edges can be created with a justification text."""
    node_repo = NodeRepository(db_session)
    edge_repo = EdgeRepository(db_session)
    n1 = await node_repo.create(concept="justify_a_test")
    n2 = await node_repo.create(concept="justify_b_test")

    edge = await edge_repo.create(
        n1.id,
        n2.id,
        "related",
        0.8,
        justification="Both mention the same underlying mechanism.",
    )
    assert edge.justification == "Both mention the same underlying mechanism."

    # Upsert updates justification
    edge2 = await edge_repo.create(
        n1.id,
        n2.id,
        "related",
        0.9,
        justification="Updated: stronger link found.",
    )
    assert edge2.id == edge.id
    assert edge2.justification == "Updated: stronger link found."


async def test_link_fact_to_edge(db_session):
    """Facts can be linked to edges."""
    from kt_db.repositories.facts import FactRepository

    node_repo = NodeRepository(db_session)
    edge_repo = EdgeRepository(db_session)
    fact_repo = FactRepository(db_session)

    n1 = await node_repo.create(concept="edge_fact_a_test")
    n2 = await node_repo.create(concept="edge_fact_b_test")
    edge = await edge_repo.create(n1.id, n2.id, "related", 0.8)
    fact = await fact_repo.create(content="Test fact for edge", fact_type="claim")

    link = await edge_repo.link_fact_to_edge(edge.id, fact.id, relevance_score=0.9)
    assert link is not None
    assert link.edge_id == edge.id
    assert link.fact_id == fact.id
    assert link.relevance_score == 0.9

    # Duplicate link returns None
    dup = await edge_repo.link_fact_to_edge(edge.id, fact.id)
    assert dup is None


async def test_get_edge_facts(db_session):
    """get_edge_facts returns linked facts."""
    from kt_db.repositories.facts import FactRepository

    node_repo = NodeRepository(db_session)
    edge_repo = EdgeRepository(db_session)
    fact_repo = FactRepository(db_session)

    n1 = await node_repo.create(concept="get_ef_a_test")
    n2 = await node_repo.create(concept="get_ef_b_test")
    edge = await edge_repo.create(n1.id, n2.id, "related", 0.7)

    f1 = await fact_repo.create(content="Fact 1", fact_type="claim")
    f2 = await fact_repo.create(content="Fact 2", fact_type="account")

    await edge_repo.link_fact_to_edge(edge.id, f1.id)
    await edge_repo.link_fact_to_edge(edge.id, f2.id)

    facts = await edge_repo.get_edge_facts(edge.id)
    assert len(facts) == 2
    fact_ids = {f.id for f in facts}
    assert f1.id in fact_ids
    assert f2.id in fact_ids


async def test_delete_edges_for_node(db_session):
    """delete_non_structural_edges removes all edges for a node."""
    node_repo = NodeRepository(db_session)
    edge_repo = EdgeRepository(db_session)
    n1 = await node_repo.create(concept="del_ns_a_test")
    n2 = await node_repo.create(concept="del_ns_b_test")
    n3 = await node_repo.create(concept="del_ns_c_test")

    await edge_repo.create(n1.id, n2.id, "related", 0.8)
    await edge_repo.create(n1.id, n3.id, "related", 0.6)

    count = await edge_repo.delete_non_structural_edges(n1.id)
    assert count == 2

    # Edges gone
    assert await edge_repo.get_edge(n1.id, n2.id, "related") is None
    assert await edge_repo.get_edge(n1.id, n3.id, "related") is None
