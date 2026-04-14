"""Qdrant wrapper for the experiment's embedding index.

Uses a dedicated collection `bigseed_experiment_paths`. Each point is a
single embedded surface form (alias or canonical) of a big-seed or path.
One big-seed typically has multiple points (one per alias).

Point id: deterministic UUID from (big_seed_id, path_id|'', source_name).
Payload: big_seed_id, path_id (empty string if flat), canonical_name,
         path_label, source_name.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from qdrant_client.models import (
    Distance,
    PointStruct,
    VectorParams,
)

from kt_config.settings import get_settings
from kt_db.keys import key_to_uuid
from kt_qdrant.client import get_qdrant_client

COLLECTION_NAME = "bigseed_experiment_paths"


@dataclass
class QdrantHit:
    big_seed_id: str
    path_id: str | None
    canonical_name: str
    path_label: str | None
    source_name: str
    score: float


def _point_id(big_seed_id: str, path_id: str | None, source_name: str) -> str:
    key = f"{big_seed_id}:{path_id or ''}:{source_name}"
    return str(key_to_uuid(key))


class QdrantIndex:
    def __init__(self, collection_name: str = COLLECTION_NAME) -> None:
        self._client = get_qdrant_client()
        self._dim = get_settings().embedding_dimensions
        self._collection = collection_name

    async def ensure(self, *, reset: bool = False) -> None:
        existing = {c.name for c in (await self._client.get_collections()).collections}
        if reset and self._collection in existing:
            await self._client.delete_collection(self._collection)
            existing.discard(self._collection)
        if self._collection not in existing:
            await self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(size=self._dim, distance=Distance.COSINE),
            )

    async def upsert(
        self,
        *,
        big_seed_id: str,
        path_id: str | None,
        canonical_name: str,
        path_label: str | None,
        source_name: str,
        vec: list[float],
    ) -> None:
        payload = {
            "big_seed_id": big_seed_id,
            "path_id": path_id or "",
            "canonical_name": canonical_name,
            "path_label": path_label or "",
            "source_name": source_name,
        }
        point = PointStruct(
            id=_point_id(big_seed_id, path_id, source_name),
            vector=vec,
            payload=payload,
        )
        await self._client.upsert(collection_name=self._collection, points=[point])

    async def delete_for(self, big_seed_id: str, path_id: str | None, source_name: str) -> None:
        await self._client.delete(
            collection_name=self._collection,
            points_selector=[_point_id(big_seed_id, path_id, source_name)],
        )

    async def search(self, vec: list[float], *, threshold: float, limit: int = 20) -> list[QdrantHit]:
        result = await self._client.query_points(
            collection_name=self._collection,
            query=vec,
            limit=limit,
            score_threshold=threshold,
            with_payload=True,
        )
        hits: list[QdrantHit] = []
        for pt in result.points:
            p = pt.payload or {}
            hits.append(
                QdrantHit(
                    big_seed_id=str(p.get("big_seed_id", "")),
                    path_id=(str(p["path_id"]) if p.get("path_id") else None),
                    canonical_name=str(p.get("canonical_name", "")),
                    path_label=(str(p["path_label"]) if p.get("path_label") else None),
                    source_name=str(p.get("source_name", "")),
                    score=float(pt.score),
                )
            )
        return hits


__all__ = ["QdrantIndex", "QdrantHit", "COLLECTION_NAME"]
