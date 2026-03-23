"""Text fact extractor — chunks text and extracts facts via LLM."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from kt_facts.extraction.base import FactExtractor
from kt_facts.models import ExtractedFactWithAttribution, ProhibitedChunk, parse_extraction_result
from kt_facts.processing.segmenter import chunk_if_needed
from kt_facts.prompt import TEXT_PROMPT_BUILDER, ExtractionPromptBuilder
from kt_models.gateway import ModelGateway

logger = logging.getLogger(__name__)


def _is_safety_error(exc: Exception) -> bool:
    """Check if an exception looks like an LLM safety filter rejection."""
    msg = str(exc).upper()
    if "SAFETY" in msg:
        return True
    if "CONTENT_FILTER" in msg:
        return True
    if "FINISH_REASON" in msg and "ERROR" in msg:
        return True
    if "PROHIBITED_CONTENT" in msg:
        return True
    return False


class TextExtractor(FactExtractor):
    """Extracts facts from text content, chunking if needed."""

    def __init__(
        self,
        gateway: ModelGateway,
        prompt_builder: ExtractionPromptBuilder | None = None,
    ) -> None:
        super().__init__(gateway, prompt_builder or TEXT_PROMPT_BUILDER)
        self._prohibited_chunks: list[ProhibitedChunk] = []

    @property
    def extractor_id(self) -> str:
        return "text"

    async def extract(
        self,
        content: str | bytes,
        concept: str,
        query_context: str | None = None,
        **kwargs: Any,
    ) -> list[Any]:
        """Extract facts from text content (chunking if needed).

        Pure LLM calls — no DB interaction. Safe to run concurrently.
        """
        source_url: str | None = kwargs.get("source_url")
        source_title: str | None = kwargs.get("source_title")
        self._prohibited_chunks = []
        text = content if isinstance(content, str) else content.decode("utf-8", errors="replace")
        chunks = chunk_if_needed(text)
        if not chunks:
            return []

        if len(chunks) == 1:
            return await self._extract_chunk(chunks[0], concept, query_context, source_url, source_title)

        chunk_results = await asyncio.gather(
            *[self._extract_chunk(chunk, concept, query_context, source_url, source_title) for chunk in chunks],
            return_exceptions=True,
        )
        extracted: list[ExtractedFactWithAttribution] = []
        for idx, result in enumerate(chunk_results):
            if isinstance(result, BaseException):
                if _is_safety_error(result):  # type: ignore[arg-type]
                    self._prohibited_chunks.append(ProhibitedChunk(
                        chunk_text=chunks[idx],
                        model_id=self._gateway.decomposition_model,
                        error_message=str(result),
                    ))
                else:
                    logger.exception("Error extracting from chunk: %s", result)
                continue
            extracted.extend(result)
        return extracted

    async def _extract_chunk(
        self,
        chunk: str,
        concept: str,
        query_context: str | None = None,
        source_url: str | None = None,
        source_title: str | None = None,
    ) -> list[Any]:
        """Extract facts from a single chunk using one LLM call."""
        prompt = self._prompt_builder.build_text_prompt(
            concept=concept,
            source_text=chunk,
            query_context=query_context,
            source_url=source_url,
            source_title=source_title,
        )

        primary_model = self._gateway.decomposition_model
        try:
            data = await self._gateway.generate_json(
                model_id=primary_model,
                messages=[{"role": "user", "content": prompt}],
                reasoning_effort=self._gateway.decomposition_thinking_level or None,
            )
            return parse_extraction_result(data)
        except Exception as exc:
            fallback_model = self._gateway.default_model
            if (
                _is_safety_error(exc)
                and fallback_model != primary_model
            ):
                logger.warning(
                    "Safety filter on %s, retrying with fallback %s",
                    primary_model,
                    fallback_model,
                )
                try:
                    data = await self._gateway.generate_json(
                        model_id=fallback_model,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    return parse_extraction_result(data)
                except Exception as fallback_exc:
                    logger.exception("Fallback model %s also failed", fallback_model)
                    self._prohibited_chunks.append(ProhibitedChunk(
                        chunk_text=chunk,
                        model_id=primary_model,
                        error_message=str(exc),
                        fallback_model_id=fallback_model,
                        fallback_error=str(fallback_exc),
                    ))
                    return []
            if _is_safety_error(exc):
                self._prohibited_chunks.append(ProhibitedChunk(
                    chunk_text=chunk,
                    model_id=primary_model,
                    error_message=str(exc),
                ))
            logger.exception("Error in LLM extraction call")
            return []
