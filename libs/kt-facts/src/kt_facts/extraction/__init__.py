"""Extraction subpackage — fact extractors for text and image content."""

from kt_facts.extraction.base import FactExtractor
from kt_facts.extraction.image import ImageExtractor
from kt_facts.extraction.text import TextExtractor

__all__ = [
    "FactExtractor",
    "ImageExtractor",
    "TextExtractor",
]
