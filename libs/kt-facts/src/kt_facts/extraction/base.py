"""Abstract base class for fact extractors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from kt_facts.prompt import ExtractionPromptBuilder
from kt_models.gateway import ModelGateway


class FactExtractor(ABC):
    """Abstract base class for fact extractors."""

    def __init__(
        self,
        gateway: ModelGateway,
        prompt_builder: ExtractionPromptBuilder,
    ) -> None:
        self._gateway = gateway
        self._prompt_builder = prompt_builder

    @property
    @abstractmethod
    def extractor_id(self) -> str: ...

    @abstractmethod
    async def extract(
        self,
        content: str | bytes,
        concept: str,
        query_context: str | None = None,
        **kwargs: Any,
    ) -> list[Any]: ...
