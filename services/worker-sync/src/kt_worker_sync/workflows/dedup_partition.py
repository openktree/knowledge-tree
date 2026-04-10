"""Pure helpers used by the fact dedup workflow.

These live in their own module (free of any Hatchet imports) so they
can be unit-tested without spinning up a Hatchet client — the Hatchet
SDK refuses to import without a fully-populated token.
"""

from __future__ import annotations

import math


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two dense vectors.

    Zero-norm inputs return ``0.0`` rather than raising.
    """
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    if da == 0.0 or db == 0.0:
        return 0.0
    return num / (da * db)


def union_find_components(n: int, edges: list[tuple[int, int]]) -> list[list[int]]:
    """Group ``n`` nodes into connected components given undirected edges.

    Returns one list of node indices per component. The order of
    components and of members within a component is unspecified —
    callers that need a stable ordering should sort at the call site.
    """
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    for a, b in edges:
        union(a, b)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())
