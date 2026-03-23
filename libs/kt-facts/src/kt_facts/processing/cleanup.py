"""Fact cleanup — validate short/incomplete facts via LLM before import."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from kt_models.gateway import ModelGateway

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 20

ProgressCallback = Callable[[str, int, int], Awaitable[None]]


class RejectedFact(BaseModel):
    """A fact that was rejected during cleanup with the reason."""

    content: str
    reason: str


@dataclass
class CleanupResult:
    """Result of the cleanup pass."""

    kept: list[Any] = field(default_factory=list)
    rejected: list[RejectedFact] = field(default_factory=list)


_EVALUATE_SYSTEM_PROMPT = """\
You are a fact quality validator. Your job is to evaluate whether candidate \
strings qualify as genuine facts worth storing in a knowledge base.

A valid fact must contain at minimum:
- A **subject** — WHO or WHAT it is about (a named entity, concept, or thing)
- A **predicate** — what the subject DOES, IS, or HAS (a verb or verb phrase)
- A **claim or observation** — what is being asserted, measured, described, or quoted

Reject strings that are:
- Bare titles, labels, or headings (e.g. "Climate Change", "Chapter 3")
- Incomplete noun phrases without a verb (e.g. "The effects of gravity")
- Sentence fragments that lack a clear assertion
- Metadata or boilerplate (e.g. "Last updated 2024", "Table of Contents")
- Duplicates of another candidate in the same batch (keep the better one)

When in doubt, KEEP the fact — do not reject borderline cases.\
"""

_EVALUATE_USER_PROMPT = """\
Evaluate each candidate below. For each, decide whether to KEEP or REJECT it.

Candidates:
{candidates}

Return JSON with exactly this structure:
{{"verdicts": [{{"index": 0, "keep": true}}, {{"index": 1, "keep": false, "reason": "bare heading with no assertion"}}]}}

Rules:
- "index" must match the candidate number (0-based).
- "keep" is a boolean.
- "reason" is required when keep=false, omit when keep=true.
- You MUST return a verdict for every candidate.\
"""


async def cleanup_facts(
    facts: Sequence[Any],
    min_words: int,
    gateway: ModelGateway,
    *,
    content_accessor: Callable[[Any], str] = lambda f: getattr(f, "content", ""),
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_progress: ProgressCallback | None = None,
) -> CleanupResult:
    """Validate short facts via LLM, passing long facts through unchanged.

    Args:
        facts: The fact objects to evaluate.
        min_words: Facts with fewer words than this are sent to the LLM.
        gateway: ModelGateway for LLM calls.
        content_accessor: Callable to extract text content from a fact object.
        batch_size: How many short facts to evaluate per LLM call.
        on_progress: Optional callback ``(phase, processed, total)``.

    Returns:
        CleanupResult with kept and rejected lists.
    """
    short: list[tuple[int, Any]] = []
    long: list[Any] = []

    for i, fact in enumerate(facts):
        text = content_accessor(fact)
        word_count = len(text.split())
        if word_count < min_words:
            short.append((i, fact))
        else:
            long.append(fact)

    if not short:
        return CleanupResult(kept=list(facts), rejected=[])

    kept: list[Any] = list(long)
    rejected: list[RejectedFact] = []
    processed = 0
    total_short = len(short)

    for batch_start in range(0, total_short, batch_size):
        batch = short[batch_start : batch_start + batch_size]
        batch_texts = [content_accessor(fact) for _, fact in batch]

        verdicts = await _evaluate_batch(batch_texts, gateway)

        for j, (_, fact) in enumerate(batch):
            if j < len(verdicts):
                keep, reason = verdicts[j]
            else:
                # Missing verdict — fail open
                keep, reason = True, ""

            if keep:
                kept.append(fact)
            else:
                rejected.append(
                    RejectedFact(
                        content=content_accessor(fact),
                        reason=reason,
                    )
                )

        processed += len(batch)
        if on_progress is not None:
            await on_progress("cleanup", processed, total_short)

    return CleanupResult(kept=kept, rejected=rejected)


async def _evaluate_batch(
    texts: list[str],
    gateway: ModelGateway,
) -> list[tuple[bool, str]]:
    """Send a batch of fact texts to the LLM for quality validation.

    Returns a list of ``(keep, reason)`` tuples in input order.
    On any error, returns all-keep (fail-open).
    """
    candidates = "\n".join(f"{i}. {text}" for i, text in enumerate(texts))
    prompt = _EVALUATE_USER_PROMPT.format(candidates=candidates)

    try:
        data = await gateway.generate_json(
            model_id=gateway.decomposition_model,
            messages=[{"role": "user", "content": prompt}],
            system_prompt=_EVALUATE_SYSTEM_PROMPT,
            temperature=0.0,
            max_tokens=4000,
            reasoning_effort=gateway.decomposition_thinking_level or None,
        )
    except Exception:
        logger.warning("Cleanup LLM call failed; keeping all %d facts (fail-open)", len(texts))
        return [(True, "")] * len(texts)

    return _parse_verdicts(data, len(texts))


def _parse_verdicts(data: dict[str, Any], expected: int) -> list[tuple[bool, str]]:
    """Parse LLM verdict response into (keep, reason) tuples.

    Missing or malformed entries default to keep=True (fail-open).
    """
    verdicts_raw = data.get("verdicts", [])
    if not isinstance(verdicts_raw, list):
        logger.warning("Cleanup LLM returned non-list verdicts; keeping all facts")
        return [(True, "")] * expected

    # Index the verdicts by their "index" field
    by_index: dict[int, tuple[bool, str]] = {}
    for v in verdicts_raw:
        if not isinstance(v, dict):
            continue
        idx = v.get("index")
        if idx is None or not isinstance(idx, int):
            continue
        keep = v.get("keep", True)
        if not isinstance(keep, bool):
            keep = True
        reason = v.get("reason", "") if not keep else ""
        if not isinstance(reason, str):
            reason = str(reason)
        by_index[idx] = (keep, reason)

    # Build ordered results, defaulting to keep for missing indices
    results: list[tuple[bool, str]] = []
    for i in range(expected):
        results.append(by_index.get(i, (True, "")))

    return results
