"""Convergence scoring for multi-model dimensions.

Compares claims across model dimensions using simple sentence-level
word-overlap matching to identify convergence and divergence.
"""

import re
from typing import TypedDict

from kt_db.models import Dimension


class ConvergenceResult(TypedDict):
    convergence_score: float
    converged_claims: list[str]
    divergent_claims: list[dict[str, object]]
    recommended_content: str


# Common English stopwords to exclude from overlap comparison
STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "can",
        "need",
        "dare",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        "out",
        "off",
        "over",
        "under",
        "again",
        "further",
        "then",
        "once",
        "and",
        "but",
        "or",
        "nor",
        "not",
        "so",
        "yet",
        "both",
        "either",
        "neither",
        "each",
        "every",
        "all",
        "any",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "no",
        "only",
        "own",
        "same",
        "than",
        "too",
        "very",
        "just",
        "because",
        "if",
        "when",
        "where",
        "how",
        "what",
        "which",
        "who",
        "whom",
        "this",
        "that",
        "these",
        "those",
        "i",
        "me",
        "my",
        "myself",
        "we",
        "our",
        "ours",
        "you",
        "your",
        "he",
        "him",
        "his",
        "she",
        "her",
        "it",
        "its",
        "they",
        "them",
        "their",
        "also",
        "about",
        "up",
        "there",
        "here",
    }
)


def _tokenize(text: str) -> set[str]:
    """Extract non-stopword lowercase tokens from text."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 1}


def _extract_claims(content: str) -> list[str]:
    """Extract individual claims (sentences) from dimension content.

    Splits on sentence boundaries (periods, semicolons, newlines, bullet points).
    Filters out very short fragments.
    """
    # Split on sentence-ending punctuation and newlines
    raw_sentences = re.split(r"[.;!\n]+", content)
    # Also split on bullet points / numbered lists
    expanded: list[str] = []
    for s in raw_sentences:
        # Split on markdown-style bullet points
        parts = re.split(r"[-*]\s+|\d+[.)]\s+", s)
        expanded.extend(parts)

    claims: list[str] = []
    for s in expanded:
        s = s.strip()
        tokens = _tokenize(s)
        # Only keep claims with enough substance (at least 3 content words)
        if len(tokens) >= 3:
            claims.append(s)
    return claims


def _claims_match(claim_a: str, claim_b: str, threshold: float = 0.6) -> bool:
    """Check if two claims match based on non-stopword token overlap.

    Two claims are considered the same if their Jaccard-like overlap
    of non-stopword tokens exceeds the threshold.
    """
    tokens_a = _tokenize(claim_a)
    tokens_b = _tokenize(claim_b)

    if not tokens_a or not tokens_b:
        return False

    intersection = tokens_a & tokens_b
    # Use the size of the smaller set for overlap ratio
    smaller = min(len(tokens_a), len(tokens_b))
    overlap = len(intersection) / smaller if smaller > 0 else 0.0
    return overlap >= threshold


def compute_convergence(dimensions: list[Dimension]) -> ConvergenceResult:
    """Compute convergence from multiple model dimensions.

    Args:
        dimensions: List of Dimension objects from different models.

    Returns:
        Dict with keys:
            - convergence_score (float): 0.0 to 1.0
            - converged_claims (list[str]): Claims present in all dimensions
            - divergent_claims (list[dict]): Claims only in some dimensions,
              each with 'claim', 'model_positions' dict
            - recommended_content (str): Summary of converged claims
    """
    if len(dimensions) == 0:
        return {
            "convergence_score": 0.0,
            "converged_claims": [],
            "divergent_claims": [],
            "recommended_content": "",
        }

    if len(dimensions) == 1:
        claims = _extract_claims(dimensions[0].content)
        return {
            "convergence_score": 1.0,
            "converged_claims": claims,
            "divergent_claims": [],
            "recommended_content": dimensions[0].content,
        }

    # Extract claims per model
    model_claims: dict[str, list[str]] = {}
    for dim in dimensions:
        model_claims[dim.model_id] = _extract_claims(dim.content)

    # Collect all unique claims (using the first model's claims as reference,
    # then adding any unmatched claims from other models)
    all_claims: list[tuple[str, str]] = []  # (claim_text, originating_model_id)
    for model_id, claims in model_claims.items():
        for claim in claims:
            # Check if this claim already exists in all_claims
            already_exists = any(_claims_match(claim, existing_claim) for existing_claim, _ in all_claims)
            if not already_exists:
                all_claims.append((claim, model_id))

    converged_claims: list[str] = []
    divergent_claims: list[dict[str, object]] = []

    for claim_text, _origin_model in all_claims:
        # Check which models have a matching claim
        supporting_models: dict[str, str] = {}
        for model_id, claims in model_claims.items():
            for c in claims:
                if _claims_match(claim_text, c):
                    supporting_models[model_id] = c
                    break

        if len(supporting_models) == len(dimensions):
            # All models agree on this claim
            converged_claims.append(claim_text)
        else:
            # Only some models have this claim
            model_positions: dict[str, str] = {}
            for model_id in model_claims:
                if model_id in supporting_models:
                    model_positions[model_id] = "supports"
                else:
                    model_positions[model_id] = "absent"
            divergent_claims.append(
                {
                    "claim": claim_text,
                    "model_positions": model_positions,
                }
            )

    total_claims = len(converged_claims) + len(divergent_claims)
    convergence_score = len(converged_claims) / total_claims if total_claims > 0 else 0.0

    recommended_content = ". ".join(converged_claims) + "." if converged_claims else ""

    return {
        "convergence_score": convergence_score,
        "converged_claims": converged_claims,
        "divergent_claims": divergent_claims,
        "recommended_content": recommended_content,
    }
