"""File-based fact extraction — PDF text extraction and image multimodal extraction."""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from kt_facts.models import ExtractedFactWithAttribution, parse_extraction_result
from kt_facts.prompt import IMAGE_PROMPT_BUILDER

if TYPE_CHECKING:
    from kt_models.gateway import ModelGateway

logger = logging.getLogger(__name__)


def classify_content_type(content_type: str) -> str:
    """Classify a MIME content-type string into a processing category.

    Returns one of: "text", "pdf", "image", "unknown".
    """
    ct = content_type.lower()
    if "application/pdf" in ct:
        return "pdf"
    if ct.startswith("image/") or "image/" in ct:
        return "image"
    if "text/" in ct or "html" in ct or "json" in ct or "xml" in ct:
        return "text"
    return "unknown"


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract text from PDF bytes using pymupdf.

    Returns concatenated text from all pages, separated by newlines.
    """
    import pymupdf  # type: ignore[import-untyped]

    pages: list[str] = []
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page in doc:
            text = page.get_text()  # type: ignore[union-attr]
            if text and text.strip():
                pages.append(text.strip())
    return "\n\n".join(pages)


@dataclass
class PdfPageResult:
    """Result of processing a single PDF page."""

    page_number: int  # 0-indexed
    text: str | None  # extracted text (None if rendered as image)
    image_bytes: bytes | None  # rendered PNG (None if text-only page)
    is_image: bool  # True = use vision model, False = use text decomposition


def extract_pdf_pages(pdf_bytes: bytes, render_dpi: int = 150) -> list[PdfPageResult]:
    """Extract PDF pages, rendering image-heavy pages as PNGs.

    For each page:
    - If the page contains embedded images → render as PNG for vision model
    - If no images → extract text as usual

    Each page is either text or image, never both.
    """
    import pymupdf  # type: ignore[import-untyped]

    results: list[PdfPageResult] = []
    image_page_count = 0
    text_page_count = 0
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page_num, page in enumerate(doc):
            images = page.get_images()  # type: ignore[union-attr]
            if images:
                image_page_count += 1
                # Page has embedded images — render as PNG
                pix = page.get_pixmap(dpi=render_dpi)  # type: ignore[union-attr]
                png_bytes = pix.tobytes("png")
                results.append(
                    PdfPageResult(
                        page_number=page_num,
                        text=None,
                        image_bytes=png_bytes,
                        is_image=True,
                    )
                )
            else:
                # Text-only page
                text_page_count += 1
                text = page.get_text()  # type: ignore[union-attr]
                results.append(
                    PdfPageResult(
                        page_number=page_num,
                        text=text.strip() if text else "",
                        image_bytes=None,
                        is_image=False,
                    )
                )
        logger.info(
            "PDF extraction: %d pages total, %d image pages, %d text pages",
            len(results),
            image_page_count,
            text_page_count,
        )
    return results


def build_image_extraction_messages(
    image_bytes: bytes,
    content_type: str,
    concept: str,
    query_context: str | None = None,
) -> list[dict[str, Any]]:
    """Build LiteLLM multimodal messages with base64-encoded image data.

    Returns a message list suitable for passing to ModelGateway.generate_json().
    """
    # Normalize content type for the data URI
    mime_type = content_type.split(";")[0].strip()
    if not mime_type.startswith("image/"):
        mime_type = "image/png"  # fallback

    b64_data = base64.b64encode(image_bytes).decode("utf-8")

    prompt_text = IMAGE_PROMPT_BUILDER.build_image_prompt(
        concept=concept,
        query_context=query_context,
    )

    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt_text},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{b64_data}",
                    },
                },
            ],
        }
    ]


async def extract_facts_from_image(
    image_bytes: bytes,
    content_type: str,
    concept: str,
    gateway: ModelGateway,
    query_context: str | None = None,
) -> tuple[list[ExtractedFactWithAttribution], str]:
    """Extract facts from an image using a multimodal vision model.

    Args:
        image_bytes: Raw image bytes.
        content_type: MIME type of the image (e.g. "image/png").
        concept: The concept being explored.
        gateway: ModelGateway for LLM calls.
        query_context: Optional investigation context.

    Returns:
        Tuple of (extracted_facts, image_description).
        image_description is a brief text summary to store as raw_content.
    """
    messages = build_image_extraction_messages(
        image_bytes,
        content_type,
        concept,
        query_context,
    )

    try:
        data = await gateway.generate_json(
            model_id=gateway.file_decomposition_model,
            messages=messages,
            reasoning_effort=gateway.file_decomposition_thinking_level or None,
        )
        facts = parse_extraction_result(data)

        # Build a description from extracted facts for storage as raw_content
        if facts:
            descriptions = [f.content for f in facts[:5]]
            description = f"[Image: {concept}] " + " | ".join(descriptions)
        else:
            description = f"[Image: {concept}] No extractable content found."

        return facts, description
    except Exception:
        logger.exception("Error extracting facts from image for concept '%s'", concept)
        return [], f"[Image: {concept}] Extraction failed."
