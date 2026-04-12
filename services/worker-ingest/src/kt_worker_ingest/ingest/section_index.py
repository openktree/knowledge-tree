"""Section indexing — chunk text and build section metadata."""

from __future__ import annotations

from dataclasses import dataclass

from kt_facts.processing.segmenter import chunk_if_needed


@dataclass
class SectionMeta:
    """Metadata for a single section/chunk."""

    section_number: int
    preview_text: str
    char_count: int


def build_section_index(text: str) -> tuple[list[str], list[SectionMeta]]:
    """Split text into sections and build an index.

    Returns:
        Tuple of (text_chunks, section_metadata).
    """
    chunks = chunk_if_needed(text)
    if not chunks:
        return [], []

    metas: list[SectionMeta] = []
    for i, chunk in enumerate(chunks):
        preview = chunk[:200].replace("\n", " ").strip()
        if len(chunk) > 200:
            preview += "..."
        metas.append(
            SectionMeta(
                section_number=i,
                preview_text=preview,
                char_count=len(chunk),
            )
        )

    return chunks, metas
