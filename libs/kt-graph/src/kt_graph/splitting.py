"""Node splitting based on divergent fact clusters.

When a node accumulates contradictory facts, it may need to be split
into perspective nodes, each linked to the original via 'related' edges.
"""

import uuid
from typing import TypedDict

from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.models import Fact, Node
from kt_db.repositories.edges import EdgeRepository
from kt_db.repositories.facts import FactRepository
from kt_db.repositories.nodes import NodeRepository
from kt_graph.convergence import _tokenize


class ClusterSummary(TypedDict):
    fact_ids: list[uuid.UUID]
    summary: str


class SplitEvaluation(TypedDict):
    should_split: bool
    clusters: list[list[uuid.UUID]]
    cluster_summaries: list[ClusterSummary]


def _fact_similarity(fact_a: Fact, fact_b: Fact) -> float:
    """Compute similarity between two facts using keyword overlap.

    Returns a Jaccard-like overlap ratio of non-stopword tokens.
    """
    tokens_a = _tokenize(fact_a.content)
    tokens_b = _tokenize(fact_b.content)

    if not tokens_a or not tokens_b:
        return 0.0

    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union) if union else 0.0


def _cluster_facts(facts: list[Fact], similarity_threshold: float = 0.15) -> list[list[Fact]]:
    """Cluster facts by keyword similarity using simple agglomerative approach.

    Facts with overlap above threshold are grouped together.
    Returns list of clusters, each containing at least one fact.
    """
    if not facts:
        return []

    clusters: list[list[Fact]] = []
    assigned: set[uuid.UUID] = set()

    for fact in facts:
        if fact.id in assigned:
            continue

        # Start a new cluster
        cluster = [fact]
        assigned.add(fact.id)

        # Find all unassigned facts that are similar to any fact in this cluster
        changed = True
        while changed:
            changed = False
            for other in facts:
                if other.id in assigned:
                    continue
                # Check similarity against any fact in the cluster
                for cluster_fact in cluster:
                    if _fact_similarity(cluster_fact, other) >= similarity_threshold:
                        cluster.append(other)
                        assigned.add(other.id)
                        changed = True
                        break

        clusters.append(cluster)

    return clusters


def _clusters_are_contradictory(clusters: list[list[Fact]]) -> bool:
    """Check if fact clusters contain contradictory content.

    A simple heuristic: if there are 2+ clusters and they have low
    cross-cluster similarity, they may represent different perspectives.
    """
    if len(clusters) < 2:
        return False

    # Check cross-cluster similarity
    for i, cluster_a in enumerate(clusters):
        for cluster_b in clusters[i + 1 :]:
            # Compute average cross-cluster similarity
            total_sim = 0.0
            count = 0
            for fa in cluster_a:
                for fb in cluster_b:
                    total_sim += _fact_similarity(fa, fb)
                    count += 1
            avg_sim = total_sim / count if count > 0 else 0.0
            # If clusters are sufficiently dissimilar, they may be contradictory
            if avg_sim < 0.1:
                return True

    return False


async def evaluate_split(
    node_id: uuid.UUID,
    session: AsyncSession,
    min_facts_for_split: int = 4,
    min_clusters: int = 2,
) -> SplitEvaluation | None:
    """Check if a node should be split based on divergent fact clusters.

    Args:
        node_id: The node to evaluate.
        session: Database session.
        min_facts_for_split: Minimum number of facts before considering a split.
        min_clusters: Minimum number of clusters to recommend a split.

    Returns:
        SplitEvaluation with 'should_split', 'clusters' (list of fact ID lists),
        'cluster_summaries' if split is recommended, or None if not.
    """
    fact_repo = FactRepository(session)
    facts = await fact_repo.get_facts_by_node(node_id)

    if len(facts) < min_facts_for_split:
        return None

    clusters = _cluster_facts(facts)

    if len(clusters) < min_clusters:
        return None

    if not _clusters_are_contradictory(clusters):
        return None

    cluster_data: list[ClusterSummary] = []
    for cluster in clusters:
        cluster_data.append(
            ClusterSummary(
                fact_ids=[f.id for f in cluster],
                summary="; ".join(f.content[:80] for f in cluster[:3]),
            )
        )

    return SplitEvaluation(
        should_split=True,
        clusters=[[f.id for f in c] for c in clusters],
        cluster_summaries=cluster_data,
    )


async def execute_split(
    node_id: uuid.UUID,
    clusters: list[list[uuid.UUID]],
    session: AsyncSession,
) -> list[Node]:
    """Split a node into perspective nodes.

    Creates new nodes (one per cluster) linked to the original via
    'related' edges. Facts from each cluster are linked to the
    corresponding new node.

    Args:
        node_id: The original node to split.
        clusters: List of fact ID lists, one per perspective.
        session: Database session.

    Returns:
        List of newly created perspective nodes.
    """
    node_repo = NodeRepository(session)
    edge_repo = EdgeRepository(session)
    fact_repo = FactRepository(session)

    original = await node_repo.get_by_id(node_id)
    if original is None:
        raise ValueError(f"Node not found: {node_id}")

    new_nodes: list[Node] = []
    for i, fact_ids in enumerate(clusters, 1):
        # Create a perspective node
        perspective_node = await node_repo.create(
            concept=f"{original.concept} (perspective {i})",
            attractor=original.attractor,
            filter_id=original.filter_id,
            max_content_tokens=original.max_content_tokens,
        )

        # Link perspective to original
        await edge_repo.create(
            source_node_id=perspective_node.id,
            target_node_id=node_id,
            relationship_type="related",
            weight=1.0,
        )

        # Link facts to the new perspective node
        for fact_id in fact_ids:
            await fact_repo.link_to_node(perspective_node.id, fact_id)

        new_nodes.append(perspective_node)

    return new_nodes
