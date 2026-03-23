"""Typo-focused analysis: which typos need phonetic fallback?

For each typo pair reports:
  - Embedding cosine similarity
  - Phonetic codes (double metaphone)
  - Whether embedding alone catches it vs needs phonetic+trigram

Usage:
    uv run --project libs/kt-models python scripts/seed_dedup/run_typo_coverage.py
"""

from __future__ import annotations

import asyncio

from datasets import TYPO_PAIRS
from utils import cosine_similarity, embed_pairs, load_settings_thresholds


async def main() -> None:
    from kt_models.embeddings import EmbeddingService

    threshold, typo_floor = load_settings_thresholds()
    svc = EmbeddingService()

    embeddings = await embed_pairs(TYPO_PAIRS, svc)

    # Try importing metaphone
    try:
        from metaphone import doublemetaphone
        has_metaphone = True
    except ImportError:
        has_metaphone = False
        print("(metaphone not installed — skipping phonetic codes)\n")

    header = f"{'Pair':<50} {'Emb':>6}  {'Zone':>12}  {'Phonetic A':>14}  {'Phonetic B':>14}  {'Match'}"
    print(header)
    print("-" * len(header))

    caught_by_embedding = 0
    caught_by_phonetic = 0
    missed = 0

    for pair in TYPO_PAIRS:
        a_emb = embeddings[pair.name_a]
        b_emb = embeddings[pair.name_b]
        score = cosine_similarity(a_emb, b_emb)

        if score >= threshold:
            zone = "embedding"
            caught_by_embedding += 1
        elif score >= typo_floor:
            zone = "gap (phon.)"
            caught_by_phonetic += 1
        else:
            zone = "MISSED"
            missed += 1

        ph_a = ph_b = ph_match = ""
        if has_metaphone:
            codes_a = doublemetaphone(pair.name_a.split()[0])
            codes_b = doublemetaphone(pair.name_b.split()[0])
            ph_a = str(codes_a[0] or codes_a[1] or "")
            ph_b = str(codes_b[0] or codes_b[1] or "")
            ph_match = "yes" if ph_a == ph_b else "no"

        pair_str = f"{pair.name_a} <-> {pair.name_b}"
        print(f"{pair_str:<50} {score:>6.4f}  {zone:>12}  {ph_a:>14}  {ph_b:>14}  {ph_match}")

    print()
    print(f"Thresholds: embedding={threshold}, typo_floor={typo_floor}")
    print(f"Caught by embedding alone: {caught_by_embedding}/{len(TYPO_PAIRS)}")
    print(f"Caught by phonetic fallback (gap zone): {caught_by_phonetic}/{len(TYPO_PAIRS)}")
    if missed:
        print(f"MISSED (below typo floor): {missed}/{len(TYPO_PAIRS)}")


if __name__ == "__main__":
    asyncio.run(main())
