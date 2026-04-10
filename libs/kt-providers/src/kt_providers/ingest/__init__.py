"""Ingest source preparation — processing, chunking, and review.

This subpackage contains the "prepare" layer of the ingest pipeline:
fetching, processing, and chunking sources into reviewable pieces.
It is used by both the API (synchronous prepare endpoint) and the
ingest worker.
"""

from kt_providers.ingest.pipeline import (
    ChunkInfo,
    ChunkSelection,
    ProcessedSource,
    build_chunk_list,
    process_ingest_sources,
    review_chunks,
)
from kt_providers.ingest.processing import PdfImagePage, ProcessedFile, process_uploaded_file
from kt_providers.ingest.section_index import SectionMeta, build_section_index

__all__ = [
    "ChunkInfo",
    "ChunkSelection",
    "PdfImagePage",
    "ProcessedFile",
    "ProcessedSource",
    "SectionMeta",
    "build_chunk_list",
    "build_section_index",
    "process_ingest_sources",
    "process_uploaded_file",
    "review_chunks",
]
