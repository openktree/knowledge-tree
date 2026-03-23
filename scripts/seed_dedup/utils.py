"""Shared helpers for seed dedup experiments."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from kt_models.embeddings import EmbeddingService

    from .datasets import SeedPair


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two vectors."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


async def embed_pairs(
    pairs: list[SeedPair],
    svc: EmbeddingService,
) -> dict[str, np.ndarray]:
    """Embed all unique names from pairs. Returns name -> embedding dict."""
    all_names = list({n for p in pairs for n in (p.name_a, p.name_b)})
    print(f"Embedding {len(all_names)} unique names...")

    embeddings: dict[str, np.ndarray] = {}
    for name in all_names:
        embeddings[name] = np.array(await svc.embed_text(name))

    return embeddings


def format_results_table(
    rows: list[tuple[str, float, str, str, str]],
    threshold: float,
) -> str:
    """Format results as an aligned table.

    Each row: (pair_str, score, would_merge, expected, status).
    """
    header = f"{'Pair':<65} {'Score':>6}  {'Result':>8}  {'Expected':>8}  {'Status'}"
    lines = [header, "-" * len(header)]

    for pair_str, score, would_merge, expected, status in rows:
        lines.append(
            f"{pair_str:<65} {score:>6.4f}  {would_merge:>8}  {expected:>8}  {status}"
        )

    return "\n".join(lines)


def load_settings_thresholds() -> tuple[float, float]:
    """Read current thresholds from settings."""
    from kt_config.settings import get_settings

    s = get_settings()
    return s.seed_dedup_embedding_threshold, s.seed_dedup_typo_floor
