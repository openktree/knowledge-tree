"""Wikidata ontology provider — walks P279 (subclass_of) chains via SPARQL.

Query strategy:
1. Search for concept by label -> get Wikidata QID
2. Walk P279 (subclass of) chain upward -> return ancestry

Uses httpx.AsyncClient with retry on 429/5xx (same pattern as providers/brave.py).
Wikidata requires a User-Agent header.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from kt_ontology.base import AncestorEntry, AncestryChain, OntologyProvider

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_BASE_DELAY = 1.0
_MAX_CHAIN_DEPTH = 15

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"

# SPARQL: search for a concept by English label, return QID + description
_SEARCH_QUERY = """\
SELECT ?item ?itemLabel ?itemDescription WHERE {{
  ?item rdfs:label "{label}"@en .
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
LIMIT 5
"""

# SPARQL: get direct P279 chain iteratively (more reliable than transitive)
_DIRECT_PARENT_QUERY = """\
SELECT ?parent ?parentLabel ?parentDescription WHERE {{
  wd:{qid} wdt:P279 ?parent .
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
LIMIT 5
"""


class WikidataOntologyProvider(OntologyProvider):
    """Wikidata-backed ontology provider using SPARQL."""

    def __init__(self, user_agent: str = "KnowledgeTree/1.0") -> None:
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": user_agent},
        )

    @property
    def provider_id(self) -> str:
        return "wikidata"

    async def get_ancestry(
        self, concept_name: str, node_type: str
    ) -> AncestryChain | None:
        if node_type == "entity":
            return None  # Entities have no ontological ancestry

        # Step 1: Find the Wikidata item
        qid = await self._search_item(concept_name)
        if qid is None:
            logger.debug("wikidata: no item found for %r", concept_name)
            return None

        # Step 2: Walk P279 chain upward
        ancestors = await self._walk_p279_chain(qid)
        if not ancestors:
            return None

        return AncestryChain(ancestors=ancestors, source=self.provider_id)

    async def is_available(self) -> bool:
        try:
            response = await self._client.head(SPARQL_ENDPOINT, timeout=5.0)
            return response.status_code < 500
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.aclose()

    # ── Internal methods ─────────────────────────────────────────

    async def _sparql_query(self, query: str) -> list[dict]:
        """Execute a SPARQL query with retry on transient failures."""
        params = {"query": query, "format": "json"}

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                response = await self._client.get(
                    SPARQL_ENDPOINT, params=params
                )
                if response.status_code not in _RETRYABLE_STATUS_CODES:
                    response.raise_for_status()
                    data = response.json()
                    return data.get("results", {}).get("bindings", [])

                last_exc = httpx.HTTPStatusError(
                    f"{response.status_code} {response.reason_phrase}",
                    request=response.request,
                    response=response,
                )
            except httpx.HTTPStatusError:
                raise
            except Exception as e:
                last_exc = e

            delay = _BASE_DELAY * (2**attempt)
            logger.warning(
                "wikidata_sparql_retrying",
                exc_info=True,
                extra={"attempt": attempt + 1, "delay": delay},
            )
            await asyncio.sleep(delay)

        if last_exc:
            raise last_exc
        return []

    async def _search_item(self, concept_name: str) -> str | None:
        """Search for a Wikidata item by English label, return QID or None."""
        # Escape quotes in concept name for SPARQL
        safe_name = concept_name.replace('"', '\\"')
        query = _SEARCH_QUERY.format(label=safe_name)

        try:
            results = await self._sparql_query(query)
        except Exception:
            logger.warning("wikidata search failed for %r", concept_name, exc_info=True)
            return None

        for binding in results:
            item_uri = binding.get("item", {}).get("value", "")
            if "/entity/Q" in item_uri:
                return item_uri.split("/")[-1]  # Extract QID

        return None

    async def _walk_p279_chain(self, start_qid: str) -> list[AncestorEntry]:
        """Iteratively walk P279 (subclass of) from start_qid upward."""
        chain: list[AncestorEntry] = []
        visited: set[str] = set()
        current_qid = start_qid

        for _ in range(_MAX_CHAIN_DEPTH):
            if current_qid in visited:
                break  # Cycle detection
            visited.add(current_qid)

            query = _DIRECT_PARENT_QUERY.format(qid=current_qid)
            try:
                results = await self._sparql_query(query)
            except Exception:
                logger.warning(
                    "wikidata P279 walk failed at %s", current_qid, exc_info=True
                )
                break

            if not results:
                break  # Reached root

            # Pick first parent (most common/general)
            binding = results[0]
            parent_uri = binding.get("parent", {}).get("value", "")
            if "/entity/Q" not in parent_uri:
                break

            parent_qid = parent_uri.split("/")[-1]
            parent_label = binding.get("parentLabel", {}).get("value", parent_qid)
            parent_desc = binding.get("parentDescription", {}).get("value")

            chain.append(
                AncestorEntry(
                    name=parent_label,
                    description=parent_desc,
                    external_id=parent_qid,
                )
            )

            current_qid = parent_qid

        return chain
