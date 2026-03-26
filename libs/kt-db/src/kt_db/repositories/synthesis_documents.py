"""Repository for synthesis document data (sentences, fact links, node links, children)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.models import (
    Fact,
    FactSource,
    Node,
    RawSource,
    SentenceFact,
    SentenceNodeLink,
    SynthesisChild,
    SynthesisSentence,
)


class SynthesisDocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bulk_create_sentences(
        self,
        synthesis_node_id: uuid.UUID,
        sentences: list[str],
    ) -> list[SynthesisSentence]:
        """Create sentence records for a synthesis document."""
        records = []
        for i, text in enumerate(sentences):
            s = SynthesisSentence(
                id=uuid.uuid4(),
                synthesis_node_id=synthesis_node_id,
                sentence_text=text,
                position=i,
            )
            self._session.add(s)
            records.append(s)
        await self._session.flush()
        return records

    async def bulk_link_sentence_facts(
        self,
        links: list[tuple[uuid.UUID, uuid.UUID, float]],
    ) -> None:
        """Create sentence-fact links. Each tuple is (sentence_id, fact_id, distance)."""
        for sentence_id, fact_id, distance in links:
            self._session.add(
                SentenceFact(
                    sentence_id=sentence_id,
                    fact_id=fact_id,
                    embedding_distance=distance,
                )
            )
        await self._session.flush()

    async def bulk_link_sentence_nodes(
        self,
        links: list[tuple[uuid.UUID, uuid.UUID, str]],
    ) -> None:
        """Create sentence-node links. Each tuple is (sentence_id, node_id, link_type)."""
        for sentence_id, node_id, link_type in links:
            self._session.add(
                SentenceNodeLink(
                    sentence_id=sentence_id,
                    node_id=node_id,
                    link_type=link_type,
                )
            )
        await self._session.flush()

    async def get_sentences(self, synthesis_node_id: uuid.UUID) -> list[SynthesisSentence]:
        """Get all sentences for a synthesis, ordered by position."""
        result = await self._session.execute(
            select(SynthesisSentence)
            .where(SynthesisSentence.synthesis_node_id == synthesis_node_id)
            .order_by(SynthesisSentence.position)
        )
        return list(result.scalars().all())

    async def get_sentence_fact_counts(self, synthesis_node_id: uuid.UUID) -> dict[uuid.UUID, int]:
        """Get fact count per sentence for a synthesis."""
        result = await self._session.execute(
            select(
                SentenceFact.sentence_id,
                func.count(SentenceFact.fact_id),
            )
            .join(SynthesisSentence, SentenceFact.sentence_id == SynthesisSentence.id)
            .where(SynthesisSentence.synthesis_node_id == synthesis_node_id)
            .group_by(SentenceFact.sentence_id)
        )
        return {row[0]: row[1] for row in result.all()}

    async def get_sentence_facts(
        self,
        sentence_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        """Get facts for a sentence, grouped by source."""
        result = await self._session.execute(
            select(SentenceFact, Fact)
            .join(Fact, SentenceFact.fact_id == Fact.id)
            .where(SentenceFact.sentence_id == sentence_id)
            .order_by(SentenceFact.embedding_distance)
        )
        rows = result.all()

        # Group by source
        facts_by_source: dict[str, dict[str, Any]] = {}
        for sf, fact in rows:
            # Get fact sources
            src_result = await self._session.execute(
                select(FactSource, RawSource)
                .join(RawSource, FactSource.raw_source_id == RawSource.id)
                .where(FactSource.fact_id == fact.id)
            )
            for fs, raw_source in src_result.all():
                source_key = str(raw_source.id)
                if source_key not in facts_by_source:
                    facts_by_source[source_key] = {
                        "source_id": source_key,
                        "source_uri": raw_source.uri,
                        "source_title": raw_source.title or "",
                        "facts": [],
                    }
                facts_by_source[source_key]["facts"].append(
                    {
                        "fact_id": str(fact.id),
                        "content": fact.content,
                        "fact_type": fact.fact_type,
                        "embedding_distance": sf.embedding_distance,
                    }
                )

        return list(facts_by_source.values())

    async def get_sentence_node_links(self, synthesis_node_id: uuid.UUID) -> list[dict[str, Any]]:
        """Get all node links for a synthesis document."""
        result = await self._session.execute(
            select(SentenceNodeLink, Node)
            .join(Node, SentenceNodeLink.node_id == Node.id)
            .join(SynthesisSentence, SentenceNodeLink.sentence_id == SynthesisSentence.id)
            .where(SynthesisSentence.synthesis_node_id == synthesis_node_id)
        )
        links = []
        for snl, node in result.all():
            links.append(
                {
                    "sentence_id": str(snl.sentence_id),
                    "node_id": str(node.id),
                    "concept": node.concept,
                    "node_type": node.node_type,
                    "link_type": snl.link_type,
                }
            )
        return links

    async def get_all_referenced_nodes(self, synthesis_node_id: uuid.UUID) -> list[Node]:
        """Get all unique nodes referenced in a synthesis document."""
        result = await self._session.execute(
            select(Node)
            .join(SentenceNodeLink, SentenceNodeLink.node_id == Node.id)
            .join(SynthesisSentence, SentenceNodeLink.sentence_id == SynthesisSentence.id)
            .where(SynthesisSentence.synthesis_node_id == synthesis_node_id)
            .distinct()
        )
        return list(result.scalars().all())

    async def add_synthesis_children(
        self,
        supersynthesis_id: uuid.UUID,
        synthesis_ids: list[uuid.UUID],
    ) -> None:
        """Link child syntheses to a supersynthesis."""
        for i, sid in enumerate(synthesis_ids):
            self._session.add(
                SynthesisChild(
                    supersynthesis_node_id=supersynthesis_id,
                    synthesis_node_id=sid,
                    position=i,
                )
            )
        await self._session.flush()

    async def get_synthesis_children(self, supersynthesis_id: uuid.UUID) -> list[Node]:
        """Get child synthesis nodes for a supersynthesis, ordered by position."""
        result = await self._session.execute(
            select(Node)
            .join(SynthesisChild, SynthesisChild.synthesis_node_id == Node.id)
            .where(SynthesisChild.supersynthesis_node_id == supersynthesis_id)
            .order_by(SynthesisChild.position)
        )
        return list(result.scalars().all())

    async def delete_document(self, synthesis_node_id: uuid.UUID) -> None:
        """Delete all document data for a synthesis (sentences cascade to links)."""
        await self._session.execute(
            delete(SynthesisSentence).where(SynthesisSentence.synthesis_node_id == synthesis_node_id)
        )
        await self._session.flush()
