"""Cached LLM + embedding helpers with per-call token accounting."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path as _Path
from typing import Any

from kt_models.embeddings import EmbeddingService
from kt_models.gateway import ModelGateway
from kt_models.usage import start_usage_tracking, stop_usage_tracking

from .big_seed import Usage


class LLMRunner:
    """Thin wrapper: call gateway, record Usage, cache JSON responses."""

    def __init__(self, gateway: ModelGateway, embedder: EmbeddingService, cache_path: _Path) -> None:
        self.gateway = gateway
        self.embedder = embedder
        self.cache_path = cache_path
        self._cache: dict[str, dict[str, Any]] = {}
        self._embed_cache: dict[str, list[float]] = {}
        self._load_cache()
        self._lock = asyncio.Lock()

    def _load_cache(self) -> None:
        if not self.cache_path.exists():
            return
        for line in self.cache_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = row.get("kind")
            key = row.get("key")
            if not key:
                continue
            if kind == "embed":
                self._embed_cache[key] = row["embedding"]
            else:
                self._cache[key] = row

    async def _append_cache(self, row: dict[str, Any]) -> None:
        async with self._lock:
            with self.cache_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")

    @staticmethod
    def _hash(kind: str, payload: dict[str, Any]) -> str:
        blob = json.dumps({"kind": kind, **payload}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    async def call_json(
        self,
        kind: str,
        system_prompt: str,
        user_content: str,
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2000,
    ) -> tuple[dict[str, Any], Usage]:
        """Run a JSON completion. Returns parsed response + Usage.

        `kind` labels the call for reporting ("alias_gen" | "multiplex").
        """
        model_id = model or self.gateway.decomposition_model
        key = self._hash(
            kind,
            {
                "model": model_id,
                "system": system_prompt,
                "user": user_content,
                "temperature": temperature,
            },
        )

        if key in self._cache:
            row = self._cache[key]
            return row["response"], Usage(
                kind=kind,
                model=row.get("model", model_id),
                prompt_tokens=row.get("prompt_tokens", 0),
                completion_tokens=row.get("completion_tokens", 0),
                cost_usd=row.get("cost_usd", 0.0),
                latency_ms=row.get("latency_ms", 0),
            )

        acc = start_usage_tracking()
        t0 = time.time()
        try:
            response = await self.gateway.generate_json(
                model_id=model_id,
                messages=[{"role": "user", "content": user_content}],
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        finally:
            stop_usage_tracking()
        latency_ms = int((time.time() - t0) * 1000)

        prompt_tokens = acc.total_prompt_tokens if acc else 0
        completion_tokens = acc.total_completion_tokens if acc else 0
        cost_usd = acc.total_cost_usd if acc else 0.0

        usage = Usage(
            kind=kind,
            model=model_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )

        row = {
            "kind": kind,
            "key": key,
            "model": model_id,
            "response": response,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_usd": cost_usd,
            "latency_ms": latency_ms,
        }
        self._cache[key] = row
        await self._append_cache(row)
        return response, usage

    async def embed(self, text: str) -> list[float]:
        """Cached single-string embedding."""
        model_id = getattr(self.embedder, "_model", "embed")
        key = self._hash("embed", {"model": model_id, "text": text})
        if key in self._embed_cache:
            return self._embed_cache[key]
        vec = await self.embedder.embed_text(text)
        self._embed_cache[key] = vec
        await self._append_cache({"kind": "embed", "key": key, "text": text, "embedding": vec})
        return vec

    async def embed_batch(self, texts: list[str]) -> None:
        """Embed many texts, populating cache. Skips anything already cached."""
        model_id = getattr(self.embedder, "_model", "embed")
        missing: list[str] = []
        seen: set[str] = set()
        for t in texts:
            if not t or t in seen:
                continue
            seen.add(t)
            k = self._hash("embed", {"model": model_id, "text": t})
            if k not in self._embed_cache:
                missing.append(t)
        if not missing:
            return
        vecs = await self.embedder.embed_batch(missing)
        for t, v in zip(missing, vecs):
            k = self._hash("embed", {"model": model_id, "text": t})
            self._embed_cache[k] = v
            await self._append_cache({"kind": "embed", "key": k, "text": t, "embedding": v})


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
