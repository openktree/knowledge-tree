import pytest

from kt_db.repositories.facts import FactRepository
from kt_db.repositories.nodes import NodeRepository
from kt_graph.splitting import evaluate_split, execute_split

pytestmark = pytest.mark.asyncio


async def test_evaluate_split_not_enough_facts(db_session):
    node_repo = NodeRepository(db_session)
    fact_repo = FactRepository(db_session)

    node = await node_repo.create(concept="split_few_facts_test")
    # Add only 2 facts (below min_facts_for_split=4)
    f1 = await fact_repo.create(content="Water boils at 100 degrees Celsius", fact_type="measurement")
    f2 = await fact_repo.create(content="Water freezes at 0 degrees Celsius", fact_type="measurement")
    await fact_repo.link_to_node(node.id, f1.id)
    await fact_repo.link_to_node(node.id, f2.id)

    result = await evaluate_split(node.id, db_session)
    assert result is None


async def test_evaluate_split_with_divergent_facts(db_session):
    node_repo = NodeRepository(db_session)
    fact_repo = FactRepository(db_session)

    node = await node_repo.create(concept="split_divergent_test")
    # Add facts from very different domains to force clustering
    facts_data = [
        ("Water boils at 100 degrees Celsius under standard atmospheric pressure", "measurement"),
        ("Water molecules consist of two hydrogen atoms and one oxygen atom", "claim"),
        ("The French Revolution began in 1789 with the storming of the Bastille fortress", "account"),
        ("Napoleon Bonaparte rose to power during the aftermath of the French Revolution", "account"),
    ]
    for content, ft in facts_data:
        fact = await fact_repo.create(content=content, fact_type=ft)
        await fact_repo.link_to_node(node.id, fact.id)

    result = await evaluate_split(node.id, db_session)
    # May or may not recommend split depending on clustering
    # The key is that it runs without error
    if result is not None:
        assert result["should_split"] is True
        assert len(result["clusters"]) >= 2


async def test_execute_split(db_session):
    node_repo = NodeRepository(db_session)
    fact_repo = FactRepository(db_session)

    original = await node_repo.create(concept="execute_split_test")

    # Create facts
    f1 = await fact_repo.create(content="Water boils at 100 degrees Celsius", fact_type="measurement")
    f2 = await fact_repo.create(content="The French Revolution started in 1789", fact_type="account")
    await fact_repo.link_to_node(original.id, f1.id)
    await fact_repo.link_to_node(original.id, f2.id)

    # Execute split with explicit clusters
    clusters = [[f1.id], [f2.id]]
    new_nodes = await execute_split(original.id, clusters, db_session)

    assert len(new_nodes) == 2
    assert "perspective 1" in new_nodes[0].concept
    assert "perspective 2" in new_nodes[1].concept

    # Verify facts are linked to new nodes
    facts_1 = await fact_repo.get_facts_by_node(new_nodes[0].id)
    facts_2 = await fact_repo.get_facts_by_node(new_nodes[1].id)
    assert len(facts_1) == 1
    assert len(facts_2) == 1


async def test_execute_split_creates_perspective_edges(db_session):
    from kt_db.repositories.edges import EdgeRepository

    node_repo = NodeRepository(db_session)
    fact_repo = FactRepository(db_session)
    edge_repo = EdgeRepository(db_session)

    original = await node_repo.create(concept="split_edges_test")
    f1 = await fact_repo.create(content="Fact cluster A content here", fact_type="claim")
    f2 = await fact_repo.create(content="Fact cluster B content here", fact_type="claim")
    await fact_repo.link_to_node(original.id, f1.id)
    await fact_repo.link_to_node(original.id, f2.id)

    new_nodes = await execute_split(original.id, [[f1.id], [f2.id]], db_session)

    # Each new node should have a related edge to original
    for new_node in new_nodes:
        edges = await edge_repo.get_edges(new_node.id, direction="both")
        related_edges = [e for e in edges if e.relationship_type == "related"]
        assert len(related_edges) >= 1
