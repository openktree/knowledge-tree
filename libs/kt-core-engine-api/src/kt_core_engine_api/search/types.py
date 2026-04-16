"""Search-result value types returned by :class:`KnowledgeProvider` implementations."""

from __future__ import annotations

from pydantic import BaseModel


class RawSearchResult(BaseModel):
    uri: str
    title: str
    raw_content: str
    provider_id: str
    provider_metadata: dict | None = None
