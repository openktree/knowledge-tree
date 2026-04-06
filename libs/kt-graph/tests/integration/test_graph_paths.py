import pytest

from kt_db.repositories.edges import EdgeRepository
from kt_db.repositories.nodes import NodeRepository
from kt_graph.read_engine import ReadGraphEngine

pytestmark = pytest.mark.asyncio


async def test_direct_connection(db_session):
    """Direct edge between two nodes should yield 1 path of length 1."""
    node_repo = NodeRepository(db_session)
    edge_repo = EdgeRepository(db_session)
    a = await node_repo.create(concept="path_direct_a")
    b = await node_repo.create(concept="path_direct_b")
    await edge_repo.create(a.id, b.id, "related", 0.8)

    engine = ReadGraphEngine(session=db_session)
    paths = await engine.find_shortest_paths(a.id, b.id)

    assert len(paths) == 1
    assert len(paths[0]) == 2  # 2 steps: source + target
    assert paths[0][0].node_id == a.id
    assert paths[0][0].edge is None
    assert paths[0][1].node_id == b.id
    assert paths[0][1].edge is not None


async def test_no_connection(db_session):
    """Two disconnected nodes should yield 0 paths."""
    node_repo = NodeRepository(db_session)
    a = await node_repo.create(concept="path_isolated_a")
    b = await node_repo.create(concept="path_isolated_b")

    engine = ReadGraphEngine(session=db_session)
    paths = await engine.find_shortest_paths(a.id, b.id)

    assert len(paths) == 0


async def test_diamond_graph(db_session):
    """Diamond: A→B→D and A→C→D should yield 2 paths of length 2."""
    node_repo = NodeRepository(db_session)
    edge_repo = EdgeRepository(db_session)
    a = await node_repo.create(concept="path_diamond_a")
    b = await node_repo.create(concept="path_diamond_b")
    c = await node_repo.create(concept="path_diamond_c")
    d = await node_repo.create(concept="path_diamond_d")
    await edge_repo.create(a.id, b.id, "related", 0.5)
    await edge_repo.create(b.id, d.id, "related", 0.5)
    await edge_repo.create(a.id, c.id, "related", 0.5)
    await edge_repo.create(c.id, d.id, "related", 0.5)

    engine = ReadGraphEngine(session=db_session)
    paths = await engine.find_shortest_paths(a.id, d.id)

    assert len(paths) == 2
    for path in paths:
        assert len(path) == 3  # A → middle → D
        assert path[0].node_id == a.id
        assert path[-1].node_id == d.id


async def test_chain_exceeds_max_depth(db_session):
    """Chain longer than max_depth should not be found."""
    node_repo = NodeRepository(db_session)
    edge_repo = EdgeRepository(db_session)
    # Build chain: n0 → n1 → n2 → n3
    nodes = []
    for i in range(4):
        nodes.append(await node_repo.create(concept=f"path_chain_depth_{i}"))
    for i in range(3):
        await edge_repo.create(nodes[i].id, nodes[i + 1].id, "related", 0.5)

    engine = ReadGraphEngine(session=db_session)
    # max_depth=2 means at most 2 edges; chain needs 3
    paths = await engine.find_shortest_paths(nodes[0].id, nodes[3].id, max_depth=2)
    assert len(paths) == 0

    # max_depth=3 should find it
    paths = await engine.find_shortest_paths(nodes[0].id, nodes[3].id, max_depth=3)
    assert len(paths) == 1
    assert len(paths[0]) == 4  # 4 steps = 3 edges


async def test_limit_cap(db_session):
    """Limit=1 on a diamond should return only 1 path."""
    node_repo = NodeRepository(db_session)
    edge_repo = EdgeRepository(db_session)
    a = await node_repo.create(concept="path_limit_a")
    b = await node_repo.create(concept="path_limit_b")
    c = await node_repo.create(concept="path_limit_c")
    d = await node_repo.create(concept="path_limit_d")
    await edge_repo.create(a.id, b.id, "related", 0.5)
    await edge_repo.create(b.id, d.id, "related", 0.5)
    await edge_repo.create(a.id, c.id, "related", 0.5)
    await edge_repo.create(c.id, d.id, "related", 0.5)

    engine = ReadGraphEngine(session=db_session)
    paths = await engine.find_shortest_paths(a.id, d.id, limit=1)

    assert len(paths) == 1


async def test_shortest_first(db_session):
    """Direct path should be returned before a 2-hop path."""
    node_repo = NodeRepository(db_session)
    edge_repo = EdgeRepository(db_session)
    a = await node_repo.create(concept="path_short_first_a")
    b = await node_repo.create(concept="path_short_first_b")
    c = await node_repo.create(concept="path_short_first_c")
    # Direct: A→B
    await edge_repo.create(a.id, b.id, "related", 0.5)
    # Indirect: A→C→B
    await edge_repo.create(a.id, c.id, "related", 0.5)
    await edge_repo.create(c.id, b.id, "related", 0.5)

    engine = ReadGraphEngine(session=db_session)
    paths = await engine.find_shortest_paths(a.id, b.id)

    assert len(paths) >= 1
    # The first path should be the direct one (length 1)
    assert len(paths[0]) == 2  # A → B


async def test_reverse_edge_traversal(db_session):
    """Edge B→A should allow finding path A→B (traversed in reverse)."""
    node_repo = NodeRepository(db_session)
    edge_repo = EdgeRepository(db_session)
    a = await node_repo.create(concept="path_reverse_a")
    b = await node_repo.create(concept="path_reverse_b")
    # Only edge is B→A, but we search A→B
    await edge_repo.create(b.id, a.id, "related", 0.7)

    engine = ReadGraphEngine(session=db_session)
    paths = await engine.find_shortest_paths(a.id, b.id)

    assert len(paths) == 1
    assert paths[0][0].node_id == a.id
    assert paths[0][1].node_id == b.id


async def test_source_equals_target(db_session):
    """Source == target should return a single zero-length path."""
    node_repo = NodeRepository(db_session)
    a = await node_repo.create(concept="path_self")

    engine = ReadGraphEngine(session=db_session)
    paths = await engine.find_shortest_paths(a.id, a.id)

    assert len(paths) == 1
    assert len(paths[0]) == 1
    assert paths[0][0].node_id == a.id
    assert paths[0][0].edge is None
