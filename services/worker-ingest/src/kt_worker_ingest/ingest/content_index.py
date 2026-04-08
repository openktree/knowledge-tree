"""Content index — hierarchical summary index for large document ingests.

Builds an index of summary entries over processed sources, enabling the
ingest agent to browse and understand document content before building nodes.

For text: chunks at ~SUMMARY_CHUNK_SIZE boundaries aligned to section breaks.
For images/PDF pages: one entry per page.
Each entry gets an LLM-generated summary and title (real heading or synthetic).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kt_models.gateway import ModelGateway
    from kt_providers.fetch import FileDataStore

from kt_worker_ingest.ingest.pipeline import ProcessedSource

logger = logging.getLogger(__name__)

# Summary chunks are larger than decomposition chunks (3K) — they provide
# overview context, not extraction precision.
SUMMARY_CHUNK_SIZE = 12_000  # ~3K tokens per summary chunk


@dataclass
class IndexEntry:
    """A single entry in the content index."""

    idx: int
    title: str  # Real heading or LLM-generated synthetic title
    summary: str  # 3-5 sentence LLM summary
    char_count: int
    source_name: str
    fact_count: int = 0  # Backfilled after decomposition
    is_image: bool = False
    # Maps to decomposition chunk indices for browse_facts scoping
    decomp_chunk_start: int = 0
    decomp_chunk_end: int = 0


@dataclass
class ContentIndex:
    """Complete content index over all processed sources."""

    entries: list[IndexEntry] = field(default_factory=list)
    total_chars: int = 0
    total_facts: int = 0
    fact_type_counts: dict[str, int] = field(default_factory=dict)

    @property
    def total_tokens_approx(self) -> int:
        """Approximate token count (chars / 4)."""
        return self.total_chars // 4

    def toc_text(self, max_entries: int = 0) -> str:
        """Build a compact table-of-contents string."""
        entries = self.entries[:max_entries] if max_entries > 0 else self.entries
        lines: list[str] = []
        for e in entries:
            tag = "[img]" if e.is_image else f"[{e.char_count:,} chars]"
            facts = f" ({e.fact_count} facts)" if e.fact_count else ""
            lines.append(f"[{e.idx}] {tag} {e.title}{facts}")
        return "\n".join(lines)


# ── Heading detection ─────────────────────────────────────────────

# Patterns for detecting headings in text
_HEADING_RE = re.compile(
    r"^(?:"
    r"#{1,4}\s+.+"  # Markdown headings
    r"|[A-Z][A-Z\s]{2,60}$"  # ALL CAPS lines (common in books)
    r"|(?:Chapter|CHAPTER|Part|PART|Book|BOOK|Section|SECTION)"
    r"\s+[\dIVXLCDM]+"  # "Chapter 1", "Part IV", etc.
    r")",
    re.MULTILINE,
)


def _detect_heading(text: str) -> str | None:
    """Try to detect a heading from the first few lines of a text chunk."""
    lines = text.strip().split("\n", 5)
    for line in lines[:3]:
        line = line.strip()
        if not line:
            continue
        # Short line that looks like a heading
        if len(line) < 80 and _HEADING_RE.match(line):
            # Clean markdown markers
            clean = re.sub(r"^#+\s+", "", line).strip()
            return clean
    return None


# ── Chunking for index ────────────────────────────────────────────


def _chunk_for_index(
    text: str,
    max_chunk: int = SUMMARY_CHUNK_SIZE,
) -> list[tuple[str, str | None]]:
    """Split text into index-sized chunks, detecting headings.

    Returns list of (chunk_text, detected_heading_or_None).
    """
    if not text or not text.strip():
        return []

    text = text.strip()
    if len(text) <= max_chunk:
        return [(text, _detect_heading(text))]

    # Split on double newlines
    paragraphs = re.split(r"\n\s*\n", text)
    chunks: list[tuple[str, str | None]] = []
    current_chunk = ""
    current_heading: str | None = None

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # Check if this paragraph starts with a heading
        heading = _detect_heading(para)

        if not current_chunk:
            current_chunk = para
            current_heading = heading or _detect_heading(para)
        elif len(current_chunk) + 2 + len(para) <= max_chunk:
            current_chunk = current_chunk + "\n\n" + para
            if current_heading is None and heading:
                current_heading = heading
        else:
            # Flush current chunk
            chunks.append((current_chunk, current_heading))
            current_chunk = para
            current_heading = heading

    if current_chunk:
        chunks.append((current_chunk, current_heading))

    return chunks


# ── LLM summarization ────────────────────────────────────────────

_SUMMARIZE_TEXT_PROMPT = """\
Read the following text section and produce a JSON response:
{
  "title": "5-10 word topic title capturing the main subject",
  "summary": "3-5 sentences summarizing: key topics covered, important entities/people mentioned, events referenced, and any notable claims or arguments."
}

Be specific — mention names, dates, and concrete concepts rather than vague descriptions.
If the section heading is provided, use it to inform (but don't just repeat) the title.

Section heading: {heading}

Text:
{text}"""

_SUMMARIZE_IMAGE_PROMPT = """\
Describe this page/image and produce a JSON response:
{
  "title": "5-10 word topic title",
  "summary": "3-5 sentences: what this page covers, key information visible, important entities or data shown."
}

Be specific — mention names, numbers, and concrete details rather than vague descriptions."""


async def _summarize_text_chunk(
    text: str,
    heading: str | None,
    gateway: ModelGateway,
) -> tuple[str, str]:
    """Summarize a text chunk, returning (title, summary)."""
    prompt = _SUMMARIZE_TEXT_PROMPT.format(
        heading=heading or "(none detected)",
        text=text[:15000],  # Cap input to avoid exceeding context
    )

    try:
        data = await gateway.generate_json(
            model_id=gateway.decomposition_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            reasoning_effort=gateway.decomposition_thinking_level or None,
        )
        title = str(data.get("title", heading or "Untitled Section"))
        summary = str(data.get("summary", ""))
        return title, summary
    except Exception:
        logger.warning("Failed to summarize text chunk", exc_info=True)
        return heading or "Untitled Section", ""


async def _summarize_image(
    image_bytes: bytes,
    content_type: str,
    source_name: str,
    gateway: ModelGateway,
) -> tuple[str, str]:
    """Summarize an image/page, returning (title, summary)."""
    mime_type = content_type.split(";")[0].strip()
    if not mime_type.startswith("image/"):
        mime_type = "image/png"

    b64_data = base64.b64encode(image_bytes).decode("utf-8")

    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": _SUMMARIZE_IMAGE_PROMPT},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{b64_data}",
                    },
                },
            ],
        }
    ]

    try:
        data = await gateway.generate_json(
            model_id=gateway.file_decomposition_model,
            messages=messages,
            max_tokens=500,
            reasoning_effort=gateway.file_decomposition_thinking_level or None,
        )
        title = str(data.get("title", source_name))
        summary = str(data.get("summary", ""))
        return title, summary
    except Exception:
        logger.warning("Failed to summarize image %s", source_name, exc_info=True)
        return source_name, ""


# ── Main builder ──────────────────────────────────────────────────


async def build_content_index(
    processed_sources: list[ProcessedSource],
    gateway: ModelGateway,
    file_data_store: FileDataStore | None = None,
    max_concurrent: int = 20,
) -> ContentIndex:
    """Build a content index from processed sources.

    For text sources: chunks at ~12K boundaries, detects headings.
    For image sources: one entry per image/page.
    All entries get LLM-generated summaries in parallel.

    Args:
        processed_sources: Already-processed ingest sources.
        gateway: Model gateway for LLM summarization calls.
        file_data_store: For accessing image bytes (required for image sources).
        max_concurrent: Max parallel summarization calls.

    Returns:
        ContentIndex with entries ready for the ingest agent.
    """
    entries: list[IndexEntry] = []
    # Tasks: (entry_idx, coroutine returning (title, summary))
    tasks: list[tuple[int, Any]] = []
    idx = 0
    total_chars = 0

    for ps in processed_sources:
        if ps.is_image:
            entry = IndexEntry(
                idx=idx,
                title=ps.name,
                summary="",
                char_count=0,
                source_name=ps.name,
                is_image=True,
            )
            entries.append(entry)

            # Schedule image summarization if we have the data
            if file_data_store and ps.raw_source_id:
                uri = f"ingest://upload/{ps.source_id.split(':')[0]}/{ps.name}"
                # Try multiple URI patterns
                image_bytes = file_data_store.get(uri)
                if image_bytes is None:
                    # Try the raw source URI pattern used by the pipeline
                    image_bytes = file_data_store.get(ps.name)
                if image_bytes:
                    ct = ps.content_type or "image/png"
                    tasks.append((idx, _summarize_image(image_bytes, ct, ps.name, gateway)))

            idx += 1
        else:
            # Text source — chunk for index
            text = ""
            if ps.is_short and ps.full_text:
                text = ps.full_text
            elif ps.sections:
                text = "\n\n".join(ps.sections)

            if not text.strip():
                continue

            text_chunks = _chunk_for_index(text)
            for chunk_text, heading in text_chunks:
                entry = IndexEntry(
                    idx=idx,
                    title=heading or f"Section {idx}",
                    summary="",
                    char_count=len(chunk_text),
                    source_name=ps.name,
                )
                entries.append(entry)
                total_chars += len(chunk_text)
                tasks.append((idx, _summarize_text_chunk(chunk_text, heading, gateway)))
                idx += 1

    # Run all summarizations in parallel
    if tasks:
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _limited(entry_idx: int, coro: Any) -> tuple[int, tuple[str, str]]:
            async with semaphore:
                result = await coro
                return entry_idx, result

        raw_results = await asyncio.gather(
            *[_limited(i, c) for i, c in tasks],
            return_exceptions=True,
        )

        for raw in raw_results:
            if isinstance(raw, BaseException):
                logger.warning("Summarization task failed: %s", raw)
                continue
            entry_idx, (title, summary) = raw
            entries[entry_idx].title = title
            entries[entry_idx].summary = summary

    return ContentIndex(
        entries=entries,
        total_chars=total_chars,
    )


def backfill_fact_counts(
    index: ContentIndex,
    total_facts: int,
    fact_type_counts: dict[str, int] | None = None,
) -> None:
    """Backfill fact_count per index entry after decomposition.

    Distributes total_facts proportionally across entries by char_count.
    For image entries, assigns a flat share.
    Also updates the index-level totals.
    """
    index.total_facts = total_facts
    if fact_type_counts:
        index.fact_type_counts = fact_type_counts

    if not index.entries or total_facts == 0:
        return

    # Distribute facts proportionally by char_count
    total_weight = sum(max(e.char_count, 1000) for e in index.entries)  # min 1000 for images
    for entry in index.entries:
        weight = max(entry.char_count, 1000) if entry.is_image else entry.char_count
        entry.fact_count = max(1, int(total_facts * weight / total_weight)) if total_weight > 0 else 0

    # Adjust rounding to match total
    assigned = sum(e.fact_count for e in index.entries)
    if assigned != total_facts and index.entries:
        diff = total_facts - assigned
        # Distribute remainder to largest entries
        sorted_entries = sorted(index.entries, key=lambda e: e.fact_count, reverse=True)
        for i in range(abs(diff)):
            if diff > 0:
                sorted_entries[i % len(sorted_entries)].fact_count += 1
            else:
                if sorted_entries[i % len(sorted_entries)].fact_count > 0:
                    sorted_entries[i % len(sorted_entries)].fact_count -= 1
