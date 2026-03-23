"""Seed disambiguation — detects and splits ambiguous seeds.

When a seed accumulates enough facts, this module checks if those facts
refer to one entity or multiple distinct entities. Uses a hybrid approach:
1. Heuristic clustering by fact embedding similarity (fast, cheap)
2. LLM disambiguation for borderline cases (accurate, expensive)
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import pdist

from kt_config.settings import get_settings
from kt_db.keys import make_seed_key

if TYPE_CHECKING:
    from kt_db.repositories.write_seeds import WriteSeedRepository
    from kt_models.embeddings import EmbeddingService
    from kt_models.gateway import ModelGateway
    from kt_qdrant.repositories.seeds import QdrantSeedRepository

logger = logging.getLogger(__name__)


async def check_disambiguation(
    seed_key: str,
    write_seed_repo: WriteSeedRepository,
    embedding_service: EmbeddingService | None = None,
    model_gateway: ModelGateway | None = None,
    write_fact_repo: object | None = None,
    qdrant_seed_repo: QdrantSeedRepository | None = None,
) -> list[dict] | None:
    """Check if a seed needs disambiguation and split if so.

    Returns:
        List of new seed dicts if split occurred, None if no split needed.
        Each dict: {"key": str, "name": str, "node_type": str, "fact_ids": [UUID]}
    """
    settings = get_settings()
    seed = await write_seed_repo.get_seed_by_key(seed_key)
    if seed is None:
        return None
    if seed.status != "active":
        return None
    if seed.fact_count < settings.seed_disambiguation_fact_threshold:
        return None

    # Load facts for this seed
    fact_ids = await write_seed_repo.get_facts_for_seed(seed_key)
    if len(fact_ids) < settings.seed_disambiguation_fact_threshold:
        return None

    # Load fact content
    facts = await _load_facts(fact_ids, write_fact_repo)
    if len(facts) < settings.seed_disambiguation_fact_threshold:
        return None

    # Layer 1: Heuristic clustering
    if embedding_service is not None:
        clusters = await _heuristic_cluster(
            facts,
            embedding_service,
            settings.seed_disambiguation_cluster_threshold,
        )
        if clusters is not None and len(clusters) > 1:
            # Clear separation — auto-split
            logger.info(
                "Heuristic split for seed '%s': %d clusters detected",
                seed_key,
                len(clusters),
            )
            return await _execute_split(
                seed_key,
                seed.name,
                seed.node_type,
                clusters,
                write_seed_repo,
                "heuristic clustering",
                embedding_service=embedding_service,
                qdrant_seed_repo=qdrant_seed_repo,
            )

    # Layer 2: LLM disambiguation (for borderline cases)
    if model_gateway is not None and embedding_service is not None:
        clusters = await _heuristic_cluster(facts, embedding_service, threshold=0.60)
        if clusters is not None and len(clusters) > 1:
            # Moderate ambiguity — ask LLM
            llm_result = await _llm_disambiguate(
                seed.name,
                seed.node_type,
                facts,
                model_gateway,
            )
            if llm_result is not None and isinstance(llm_result, dict):
                clusters_out = llm_result["clusters"]
                labels = llm_result["labels"]
                if len(clusters_out) > 1:
                    logger.info(
                        "LLM split for seed '%s': %d groups identified",
                        seed_key,
                        len(clusters_out),
                    )
                    return await _execute_split(
                        seed_key,
                        seed.name,
                        seed.node_type,
                        clusters_out,
                        write_seed_repo,
                        "LLM disambiguation",
                        labels=labels,
                        embedding_service=embedding_service,
                        qdrant_seed_repo=qdrant_seed_repo,
                    )

    return None


async def _load_facts(
    fact_ids: list[uuid.UUID],
    write_fact_repo: object | None = None,
) -> list[dict]:
    """Load fact content from write-db repository.

    Returns list of {"id": UUID, "content": str} dicts.
    """
    if write_fact_repo is None:
        return []

    facts = []
    try:
        loaded = await write_fact_repo.get_by_ids(fact_ids)  # type: ignore[union-attr]
        for f in loaded:
            facts.append({"id": f.id, "content": f.content})
    except Exception:
        logger.debug("Failed to load facts for disambiguation", exc_info=True)

    return facts


async def _heuristic_cluster(
    facts: list[dict],
    embedding_service: EmbeddingService,
    threshold: float,
) -> list[list[dict]] | None:
    """Cluster facts by embedding similarity using hierarchical clustering.

    Returns:
        List of clusters (each a list of fact dicts), or None if clustering
        finds only one cluster or fails.
    """
    if len(facts) < 3:
        return None

    try:
        contents = [f["content"] for f in facts]
        embeddings = await embedding_service.embed_batch(contents)

        if not embeddings or len(embeddings) != len(facts):
            return None

        # Convert to numpy array
        emb_array = np.array(embeddings)

        # Compute pairwise cosine distances
        distances = pdist(emb_array, metric="cosine")

        # Hierarchical clustering
        Z = linkage(distances, method="average")

        # Cut at threshold (cosine distance, so 1 - similarity)
        distance_threshold = 1.0 - threshold
        labels = fcluster(Z, t=distance_threshold, criterion="distance")

        # Group facts by cluster
        clusters: dict[int, list[dict]] = {}
        for fact, label in zip(facts, labels):
            clusters.setdefault(int(label), []).append(fact)

        cluster_list = list(clusters.values())

        # Only return if we have multiple non-trivial clusters
        # (at least 2 facts in each cluster)
        significant = [c for c in cluster_list if len(c) >= 2]
        if len(significant) <= 1:
            return None

        return significant

    except Exception:
        logger.debug("Heuristic clustering failed", exc_info=True)
        return None


async def _llm_disambiguate(
    seed_name: str,
    node_type: str,
    facts: list[dict],
    model_gateway: ModelGateway,
) -> dict | None:
    """Ask LLM to group facts into distinct entities/concepts.

    Returns dict with "clusters" and "labels" keys, or None if LLM says
    all facts refer to one entity.
    """
    try:
        fact_lines = []
        for i, f in enumerate(facts, 1):
            fact_lines.append(f"{i}. {f['content'][:300]}")

        prompt = (
            f'The following facts were all extracted and tagged with the mention "{seed_name}" '
            f"(type: {node_type}). Determine whether these facts refer to ONE entity/concept "
            f"or MULTIPLE distinct entities/concepts with the same name.\n\n"
            f"Facts:\n" + "\n".join(fact_lines) + "\n\n"
            "Respond with JSON:\n"
            '{"is_ambiguous": true/false, "groups": [{"label": "disambiguation label", "fact_numbers": [1, 2, ...]}]}\n\n'
            "If all facts refer to one entity, set is_ambiguous=false and put all fact numbers in one group.\n"
            "If facts refer to multiple entities, set is_ambiguous=true and create a group for each distinct entity "
            'with a descriptive label that disambiguates it (e.g., "Mars (planet)", "Mars (Roman god)").'
        )

        result = await model_gateway.generate_json(
            model_id=model_gateway.default_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )

        if not isinstance(result, dict):
            return None

        if not result.get("is_ambiguous", False):
            return None

        groups = result.get("groups", [])
        if not isinstance(groups, list) or len(groups) <= 1:
            return None

        # Map fact numbers back to fact dicts
        fact_by_num = {i + 1: f for i, f in enumerate(facts)}
        clusters: list[list[dict]] = []
        labels: list[str] = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            fact_nums = group.get("fact_numbers", [])
            cluster_facts = [fact_by_num[n] for n in fact_nums if n in fact_by_num]
            if cluster_facts:
                clusters.append(cluster_facts)
                labels.append(group.get("label", ""))

        if len(clusters) <= 1:
            return None

        return {"clusters": clusters, "labels": labels}

    except Exception:
        logger.debug("LLM disambiguation failed for '%s'", seed_name, exc_info=True)
        return None


async def _execute_split(
    original_key: str,
    original_name: str,
    node_type: str,
    clusters: list[list[dict]],
    write_seed_repo: WriteSeedRepository,
    reason: str,
    labels: list[str] | None = None,
    embedding_service: EmbeddingService | None = None,
    qdrant_seed_repo: QdrantSeedRepository | None = None,
) -> list[dict]:
    """Execute the actual seed split.

    Creates new seeds for each cluster and reassigns facts.
    When labels are provided (from LLM), uses them for disambiguated names.
    Computes representative embeddings for each child and upserts to Qdrant.
    """
    new_seeds: list[dict] = []
    fact_assignments: dict[str, list[uuid.UUID]] = {}

    for i, cluster in enumerate(clusters):
        # Use LLM label if available, otherwise fall back to "variant N"
        if labels and i < len(labels) and labels[i]:
            label = labels[i]
            disambiguated_name = f"{original_name} ({label})"
        else:
            label = f"variant {i + 1}"
            disambiguated_name = f"{original_name} ({label})"

        new_key = make_seed_key(node_type, disambiguated_name)
        cluster_fact_ids = [f["id"] for f in cluster]

        new_seeds.append(
            {
                "key": new_key,
                "name": disambiguated_name,
                "node_type": node_type,
                "label": label,
            }
        )
        fact_assignments[new_key] = cluster_fact_ids

    await write_seed_repo.split_seed(
        original_key=original_key,
        new_seeds=new_seeds,
        fact_assignments=fact_assignments,
        reason=reason,
    )

    # Compute representative embeddings for each child (centroid of fact embeddings)
    if embedding_service is not None and qdrant_seed_repo is not None:
        try:
            for seed_data, cluster in zip(new_seeds, clusters):
                contents = [f["content"] for f in cluster if f.get("content")]
                if not contents:
                    continue
                embeddings = await embedding_service.embed_batch(contents)
                if not embeddings:
                    continue
                # Compute centroid
                centroid = np.mean(np.array(embeddings), axis=0).tolist()
                await qdrant_seed_repo.upsert(
                    seed_key=seed_data["key"],
                    embedding=centroid,
                    name=seed_data["name"],
                    node_type=node_type,
                )
        except Exception:
            logger.debug("Failed to compute representative embeddings for split", exc_info=True)

    return new_seeds
