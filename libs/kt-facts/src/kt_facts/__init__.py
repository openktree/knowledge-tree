"""Fact decomposition and extraction package."""

from kt_facts.extraction import (
    FactExtractor,
    ImageExtractor,
    TextExtractor,
)
from kt_facts.models import (
    ExtractedFactWithAttribution,
    SourceExtractionResult,
    parse_extraction_result,
)
from kt_facts.pipeline import DecompositionPipeline, DecompositionResult
from kt_facts.prompt import (
    IMAGE_PROMPT_BUILDER,
    TEXT_PROMPT_BUILDER,
    ExtractionPromptBuilder,
)

__all__ = [
    "DecompositionPipeline",
    "DecompositionResult",
    "ExtractedFactWithAttribution",
    "ExtractionPromptBuilder",
    "FactExtractor",
    "IMAGE_PROMPT_BUILDER",
    "ImageExtractor",
    "TEXT_PROMPT_BUILDER",
    "TextExtractor",
    "SourceExtractionResult",
    "parse_extraction_result",
]
