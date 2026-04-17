"""LLM-driven per-fact entity extraction.

Exhaustively extracts nodes from a batch of facts with bounded concurrency.
Used by :class:`LlmEntityExtractor`; exposed as a module function so tests
can exercise the extraction step independently of the `EntityExtractor` ABC.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from kt_core_engine_api.extractor import is_valid_entity_name
from kt_models.gateway import ModelGateway

from .llm_prompts import NODE_EXTRACTION_SYSTEM, NODE_EXTRACTION_USER

logger = logging.getLogger(__name__)


async def extract_entities_from_facts(
    facts: list,
    gateway: ModelGateway,
    *,
    scope: str = "",
    batch_size: int | None = None,
) -> list[dict[str, Any]] | None:
    """Extract nodes from *facts* via LLM, batching + merging across calls."""
    if not facts:
        return None

    from kt_config.settings import get_settings

    _settings = get_settings()
    resolved_batch = batch_size if batch_size is not None else _settings.entity_extraction_batch_size
    max_concurrent = _settings.entity_extraction_concurrency

    batches: list[list] = [facts[i : i + resolved_batch] for i in range(0, len(facts), resolved_batch)]

    logger.info(
        "Node extraction: %d facts → %d batches of ≤%d (concurrency=%d)",
        len(facts),
        len(batches),
        resolved_batch,
        max_concurrent,
    )

    sem = asyncio.Semaphore(max_concurrent)
    completed = 0

    async def _limited(batch: list, offset: int) -> list[dict[str, Any]] | None:
        async with sem:
            result = await _extract_entity_batch(batch, offset=offset, gateway=gateway, scope=scope)
            nonlocal completed
            completed += 1
            logger.info(
                "Entity extraction batch %d/%d done (%d facts)",
                completed,
                len(batches),
                len(batch),
            )
            return result

    tasks = [_limited(batch, offset=i * resolved_batch) for i, batch in enumerate(batches)]
    batch_results = await asyncio.gather(*tasks, return_exceptions=True)

    merged: dict[str, dict[str, Any]] = {}
    for br in batch_results:
        if isinstance(br, BaseException):
            logger.warning("Extraction batch failed: %s", br)
            continue
        if not br:
            continue
        for node in br:
            key = node["name"].strip().lower()
            if key in merged:
                existing_indices = set(merged[key].get("fact_indices", []))
                new_indices = set(node.get("fact_indices", []))
                merged[key]["fact_indices"] = sorted(existing_indices | new_indices)
                existing_aliases = set(merged[key].get("aliases", []))
                new_aliases = set(node.get("aliases", []))
                merged[key]["aliases"] = sorted(existing_aliases | new_aliases)
            else:
                merged[key] = node

    valid = list(merged.values())
    return valid if valid else None


async def _extract_entity_batch(
    batch: list,
    *,
    offset: int,
    gateway: ModelGateway,
    scope: str,
) -> list[dict[str, Any]] | None:
    lines: list[str] = []
    for i, f in enumerate(batch, offset + 1):
        lines.append(f"{i}. [{f.fact_type}] {f.content}")
    fact_list = "\n".join(lines)

    user_msg = NODE_EXTRACTION_USER.format(
        fact_count=len(batch),
        scope=scope or "general",
        fact_list=fact_list,
    )

    try:
        result = await gateway.generate_json(
            model_id=gateway.entity_extraction_model,
            messages=[{"role": "user", "content": user_msg}],
            system_prompt=NODE_EXTRACTION_SYSTEM,
            temperature=0.0,
            max_tokens=16000,
            reasoning_effort=gateway.entity_extraction_thinking_level or None,
        )
        if not result:
            return None
        return _parse_per_fact_result(result, offset=offset, batch_size=len(batch))
    except Exception:
        logger.warning(
            "Failed to extract nodes from fact batch (offset=%d, size=%d)",
            offset,
            len(batch),
            exc_info=True,
        )
        return None


def _parse_per_fact_result(
    result: dict[str, Any],
    *,
    offset: int,
    batch_size: int,
) -> list[dict[str, Any]] | None:
    facts_data = result.get("facts")
    if not isinstance(facts_data, dict):
        logger.warning(
            "LLM did not return expected per-fact format (got keys: %s); discarding batch",
            list(result.keys()),
        )
        return None

    merged: dict[str, dict[str, Any]] = {}

    for fact_key, entities in facts_data.items():
        try:
            fact_idx = int(fact_key)
        except (ValueError, TypeError):
            continue
        if fact_idx < offset + 1 or fact_idx > offset + batch_size:
            continue
        if not isinstance(entities, list):
            continue

        for entry in entities:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not name or not isinstance(name, str):
                continue

            name = name.strip()
            if not is_valid_entity_name(name):
                continue
            norm_key = name.lower()

            node_type = entry.get("node_type", "concept")
            if node_type not in ("concept", "entity", "event", "location"):
                node_type = "concept"

            if norm_key in merged:
                merged[norm_key]["fact_indices"].append(fact_idx)
                for alias in entry.get("aliases", []):
                    if isinstance(alias, str) and alias.strip():
                        a = alias.strip()
                        if a not in merged[norm_key]["aliases"]:
                            merged[norm_key]["aliases"].append(a)
            else:
                node_dict: dict[str, Any] = {
                    "name": name,
                    "node_type": node_type,
                    "fact_indices": [fact_idx],
                    "aliases": [a.strip() for a in entry.get("aliases", []) if isinstance(a, str) and a.strip()],
                }
                if node_type == "entity":
                    subtype = entry.get("entity_subtype", "other")
                    if subtype not in ("person", "organization", "other"):
                        subtype = "other"
                    node_dict["entity_subtype"] = subtype
                merged[norm_key] = node_dict

    valid = list(merged.values())
    return valid if valid else None
