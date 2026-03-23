"""Shared data models for the fact extraction pipeline.

These are extracted from pipeline.py to break the circular dependency
between pipeline.py and extractors.py.
"""

from __future__ import annotations

from pydantic import BaseModel

from kt_config.types import FactType

_VALID_TYPES = {ft.value for ft in FactType}


# ── Data models ────────────────────────────────────────────────────


class ExtractedFactWithAttribution(BaseModel):
    """A single extracted fact with inline attribution."""

    content: str
    fact_type: str
    who: str | None = None
    where: str | None = None
    when: str | None = None
    context: str | None = None


class SourceExtractionResult(BaseModel):
    """Result of extracting facts from a single source."""

    facts: list[ExtractedFactWithAttribution] = []


# ── Pure functions ─────────────────────────────────────────────────


def parse_extraction_result(data: dict | list) -> list[ExtractedFactWithAttribution]:  # type: ignore[type-arg]
    """Parse the JSON response into ExtractedFactWithAttribution objects."""
    if isinstance(data, list):
        raw_facts = data
    else:
        raw_facts = data.get("facts", [])
    if not isinstance(raw_facts, list):
        return []

    results: list[ExtractedFactWithAttribution] = []
    for item in raw_facts:
        if not isinstance(item, dict) or "content" not in item:
            continue

        def _clean(val: object) -> str | None:
            if val is None:
                return None
            s = str(val).strip()
            if s.lower() in ("null", "none", "n/a", ""):
                return None
            return s

        results.append(
            ExtractedFactWithAttribution(
                content=item["content"],
                fact_type=item.get("fact_type", "claim"),
                who=_clean(item.get("who")),
                where=_clean(item.get("where")),
                when=_clean(item.get("when")),
                context=_clean(item.get("context")),
            )
        )
    return results


# Keep private alias for backwards compat (used in tests and file_processing)
_parse_extraction_result = parse_extraction_result


class ProhibitedChunk(BaseModel):
    """A chunk rejected by LLM safety filters during fact extraction."""

    chunk_text: str
    model_id: str
    error_message: str
    fallback_model_id: str | None = None
    fallback_error: str | None = None


def _format_attribution(ef: ExtractedFactWithAttribution) -> str | None:
    """Format attribution fields into a human-readable string."""
    parts: list[str] = []
    if ef.who:
        parts.append(f"who: {ef.who}")
    if ef.where:
        parts.append(f"where: {ef.where}")
    if ef.when:
        parts.append(f"when: {ef.when}")
    if ef.context:
        parts.append(f"context: {ef.context}")

    return "; ".join(parts) if parts else None
