"""Processing subpackage — segmentation, deduplication, cleanup, and file handling."""

from kt_facts.processing.cleanup import cleanup_facts
from kt_facts.processing.dedup import (
    InsertFactsPendingResult,
    insert_facts_pending,
)
from kt_facts.processing.file_processing import (
    classify_content_type,
    extract_pdf_pages,
    extract_text_from_pdf,
)
from kt_facts.processing.segmenter import chunk_if_needed, segment_text

__all__ = [
    "InsertFactsPendingResult",
    "chunk_if_needed",
    "classify_content_type",
    "cleanup_facts",
    "extract_pdf_pages",
    "extract_text_from_pdf",
    "insert_facts_pending",
    "segment_text",
]
