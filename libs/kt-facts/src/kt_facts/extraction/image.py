"""Image fact extractor — extracts facts from images via multimodal vision model."""

from __future__ import annotations

import base64
import logging
from typing import Any

from kt_facts.extraction.base import FactExtractor
from kt_facts.models import parse_extraction_result
from kt_facts.prompt import IMAGE_PROMPT_BUILDER, ExtractionPromptBuilder
from kt_models.gateway import ModelGateway

logger = logging.getLogger(__name__)


class ImageExtractor(FactExtractor):
    """Extracts facts from images via multimodal vision model."""

    def __init__(
        self,
        gateway: ModelGateway,
        prompt_builder: ExtractionPromptBuilder | None = None,
    ) -> None:
        super().__init__(gateway, prompt_builder or IMAGE_PROMPT_BUILDER)

    @property
    def extractor_id(self) -> str:
        return "image"

    async def extract(
        self,
        content: str | bytes,
        concept: str,
        query_context: str | None = None,
        **kwargs: Any,
    ) -> list[Any]:
        """Extract facts from an image using a multimodal vision model.

        Args:
            content: Raw image bytes.
            concept: The concept being explored.
            query_context: Optional investigation context.
            **kwargs: Must include 'content_type' (MIME type string).

        Returns:
            List of ExtractedFactWithAttribution objects.
        """
        image_bytes = content if isinstance(content, bytes) else content.encode("utf-8")
        content_type: str = kwargs.get("content_type", "image/png")

        prompt_text = self._prompt_builder.build_image_prompt(
            concept=concept,
            query_context=query_context,
        )
        messages = self._build_multimodal_messages(image_bytes, content_type, prompt_text)

        try:
            data = await self._gateway.generate_json(
                model_id=self._gateway.file_decomposition_model,
                messages=messages,
                reasoning_effort=self._gateway.file_decomposition_thinking_level or None,
            )
            return parse_extraction_result(data)
        except Exception:
            logger.exception("Error extracting facts from image for concept '%s'", concept)
            return []

    async def extract_with_description(
        self,
        image_bytes: bytes,
        content_type: str,
        concept: str,
        query_context: str | None = None,
    ) -> tuple[list[Any], str]:
        """Extract facts from image and return (facts, description).

        The description is a text summary suitable for storing as raw_content.
        """
        facts = await self.extract(
            image_bytes,
            concept,
            query_context,
            content_type=content_type,
        )

        if facts:
            descriptions = [f.content for f in facts[:5]]
            description = f"[Image: {concept}] " + " | ".join(descriptions)
        else:
            description = f"[Image: {concept}] No extractable content found."

        return facts, description

    @staticmethod
    def _build_multimodal_messages(
        image_bytes: bytes,
        content_type: str,
        prompt_text: str,
    ) -> list[dict[str, Any]]:
        """Build LiteLLM multimodal messages with base64-encoded image data."""
        mime_type = content_type.split(";")[0].strip()
        if not mime_type.startswith("image/"):
            mime_type = "image/png"

        b64_data = base64.b64encode(image_bytes).decode("utf-8")

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
