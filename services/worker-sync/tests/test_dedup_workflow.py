"""Unit tests for the pure helpers used by the fact dedup workflow.

The Hatchet-aware part of ``kt_worker_sync.workflows.dedup_pending_facts``
requires a fully-configured Hatchet client at import time; end-to-end
coverage for that lives next to the sync integration suite. These
tests target the pure helpers in
``kt_worker_sync.workflows.dedup_partition`` — cosine similarity and
union-find component grouping — which are the load-bearing pieces of
the partitioning step.
"""

from __future__ import annotations

import pytest

from kt_worker_sync.workflows.dedup_partition import (
    cosine,
    union_find_components,
)


def test_cosine_identical_vectors() -> None:
    assert cosine([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == pytest.approx(1.0)


def test_cosine_orthogonal() -> None:
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_zero_vector() -> None:
    # Zero vectors do not raise; the function returns 0 by convention
    # so the partition step does not accidentally link unrelated rows.
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_cosine_negative_vectors() -> None:
    assert cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_union_find_no_edges_gives_singletons() -> None:
    comps = union_find_components(4, [])
    assert sorted(map(sorted, comps)) == [[0], [1], [2], [3]]


def test_union_find_single_component() -> None:
    comps = union_find_components(4, [(0, 1), (1, 2), (2, 3)])
    assert len(comps) == 1
    assert sorted(comps[0]) == [0, 1, 2, 3]


def test_union_find_two_components() -> None:
    comps = union_find_components(4, [(0, 1), (2, 3)])
    assert len(comps) == 2
    sorted_comps = sorted(sorted(c) for c in comps)
    assert sorted_comps == [[0, 1], [2, 3]]


def test_union_find_empty() -> None:
    assert union_find_components(0, []) == []


def test_partition_components_matches_expected_clusters() -> None:
    """The load-bearing invariant: embeddings for (a,b) are close,
    (c,d) are close, (a,c) are far — we expect exactly two components
    {a,b} and {c,d}, so parallel merge of the two is safe.
    """
    emb_a = [1.0, 0.0, 0.0]
    emb_b = [0.99, 0.01, 0.0]
    emb_c = [0.0, 1.0, 0.0]
    emb_d = [0.01, 0.99, 0.0]
    embeddings = [emb_a, emb_b, emb_c, emb_d]

    n = len(embeddings)
    threshold = 0.9
    edges: list[tuple[int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            if cosine(embeddings[i], embeddings[j]) >= threshold:
                edges.append((i, j))

    comps = union_find_components(n, edges)
    comp_sets = sorted(sorted(c) for c in comps)
    assert comp_sets == [[0, 1], [2, 3]]


def test_partition_components_chain_merges() -> None:
    """A close to B, B close to C, but A far from C — they should
    still all land in the same component via transitive union.
    """
    emb_a = [1.0, 0.0, 0.0]
    emb_b = [0.95, 0.31, 0.0]  # close enough to A
    emb_c = [0.7, 0.71, 0.0]  # close to B but not to A
    embeddings = [emb_a, emb_b, emb_c]
    n = len(embeddings)
    threshold = 0.85

    edges: list[tuple[int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            if cosine(embeddings[i], embeddings[j]) >= threshold:
                edges.append((i, j))

    comps = union_find_components(n, edges)
    assert len(comps) == 1
    assert sorted(comps[0]) == [0, 1, 2]
