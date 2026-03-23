"""Process uploaded files — classify and extract text or prepare for vision model."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from kt_facts.processing.file_processing import classify_content_type, extract_pdf_pages
from kt_providers.fetcher import FileDataStore

logger = logging.getLogger(__name__)


@dataclass
class PdfImagePage:
    """A PDF page rendered as PNG for vision model processing."""

    page_number: int  # 0-indexed
    image_bytes: bytes


@dataclass
class ProcessedFile:
    """Result of processing an uploaded file."""

    text: str | None  # Extracted text (None for images)
    is_image: bool
    content_type: str
    pdf_image_pages: list[PdfImagePage] = field(default_factory=list)


async def process_uploaded_file(
    file_bytes: bytes,
    mime_type: str,
    uri: str,
    file_data_store: FileDataStore,
) -> ProcessedFile:
    """Classify and process an uploaded file.

    - PDF: extract pages, rendering image-heavy pages as PNG for vision model
    - TXT/text: direct decode
    - Image: store bytes in FileDataStore for vision model processing
    """
    classified = classify_content_type(mime_type)

    if classified == "pdf":
        pages = extract_pdf_pages(file_bytes)
        # Concatenate text from text-only pages
        text_parts: list[str] = []
        image_pages: list[PdfImagePage] = []
        for page in pages:
            if page.is_image and page.image_bytes:
                image_pages.append(PdfImagePage(
                    page_number=page.page_number,
                    image_bytes=page.image_bytes,
                ))
            elif page.text:
                text_parts.append(page.text)
        text = "\n\n".join(text_parts) if text_parts else None
        logger.info(
            "PDF processed: %d text pages, %d image pages, text length=%d",
            len(text_parts), len(image_pages), len(text) if text else 0,
        )
        return ProcessedFile(
            text=text,
            is_image=False,
            content_type=mime_type,
            pdf_image_pages=image_pages,
        )

    if classified == "image":
        file_data_store.store(uri, file_bytes)
        return ProcessedFile(text=None, is_image=True, content_type=mime_type)

    # Default: treat as text
    try:
        text = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = file_bytes.decode("latin-1")
    return ProcessedFile(text=text, is_image=False, content_type=mime_type)
