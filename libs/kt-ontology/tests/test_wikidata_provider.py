"""Tests for the WikidataOntologyProvider SPARQL response parsing."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from kt_ontology.base import AncestorEntry
from kt_ontology.wikidata import WikidataOntologyProvider


def _make_sparql_response(bindings: list[dict], status: int = 200) -> httpx.Response:
    """Create a mock SPARQL response."""
    return httpx.Response(
        status_code=status,
        json={"results": {"bindings": bindings}},
        request=httpx.Request("GET", "https://query.wikidata.org/sparql"),
    )


def _binding(
    item_key: str,
    item_value: str,
    label_key: str | None = None,
    label_value: str | None = None,
    desc_key: str | None = None,
    desc_value: str | None = None,
) -> dict:
    """Create a single SPARQL binding."""
    b: dict = {
        item_key: {"type": "uri", "value": item_value},
    }
    if label_key and label_value:
        b[label_key] = {"type": "literal", "value": label_value}
    if desc_key and desc_value:
        b[desc_key] = {"type": "literal", "value": desc_value}
    return b


@pytest.mark.asyncio
class TestWikidataSearchItem:
    async def test_search_finds_qid(self) -> None:
        provider = WikidataOntologyProvider()
        mock_response = _make_sparql_response([
            _binding("item", "http://www.wikidata.org/entity/Q5294",
                     "itemLabel", "sorting algorithm"),
        ])

        provider._client = AsyncMock()
        provider._client.get = AsyncMock(return_value=mock_response)

        qid = await provider._search_item("sorting algorithm")
        assert qid == "Q5294"

    async def test_search_no_results(self) -> None:
        provider = WikidataOntologyProvider()
        mock_response = _make_sparql_response([])

        provider._client = AsyncMock()
        provider._client.get = AsyncMock(return_value=mock_response)

        qid = await provider._search_item("nonexistent concept xyz")
        assert qid is None

    async def test_search_skips_non_entity_uris(self) -> None:
        provider = WikidataOntologyProvider()
        mock_response = _make_sparql_response([
            _binding("item", "http://example.org/not-an-entity"),
        ])

        provider._client = AsyncMock()
        provider._client.get = AsyncMock(return_value=mock_response)

        qid = await provider._search_item("test")
        assert qid is None


@pytest.mark.asyncio
class TestWikidataP279Walk:
    async def test_walk_chain(self) -> None:
        provider = WikidataOntologyProvider()

        # Step 1: Q5294 -> Q8366 (algorithm)
        resp1 = _make_sparql_response([
            _binding("parent", "http://www.wikidata.org/entity/Q8366",
                     "parentLabel", "algorithm",
                     "parentDescription", "sequence of instructions"),
        ])
        # Step 2: Q8366 -> Q21198 (computer science)
        resp2 = _make_sparql_response([
            _binding("parent", "http://www.wikidata.org/entity/Q21198",
                     "parentLabel", "computer science"),
        ])
        # Step 3: Q21198 -> no more parents
        resp3 = _make_sparql_response([])

        provider._client = AsyncMock()
        provider._client.get = AsyncMock(side_effect=[resp1, resp2, resp3])

        chain = await provider._walk_p279_chain("Q5294")
        assert len(chain) == 2
        assert chain[0].name == "algorithm"
        assert chain[0].external_id == "Q8366"
        assert chain[1].name == "computer science"
        assert chain[1].external_id == "Q21198"

    async def test_walk_handles_cycle(self) -> None:
        """Cycle detection should break the walk."""
        provider = WikidataOntologyProvider()

        # Q1 -> Q2 -> Q1 (cycle)
        resp1 = _make_sparql_response([
            _binding("parent", "http://www.wikidata.org/entity/Q2",
                     "parentLabel", "B"),
        ])
        resp2 = _make_sparql_response([
            _binding("parent", "http://www.wikidata.org/entity/Q1",
                     "parentLabel", "A"),
        ])

        provider._client = AsyncMock()
        provider._client.get = AsyncMock(side_effect=[resp1, resp2])

        # Start at Q1 — visits Q1, gets parent Q2, then visits Q2, gets parent Q1
        # Cycle detected on third iteration when Q1 is already visited
        chain = await provider._walk_p279_chain("Q1")
        assert len(chain) == 2  # Q2 (B) and Q1 (A), then cycle detected
        assert chain[0].name == "B"
        assert chain[1].name == "A"


@pytest.mark.asyncio
class TestWikidataGetAncestry:
    async def test_entity_returns_none(self) -> None:
        provider = WikidataOntologyProvider()
        result = await provider.get_ancestry("Elon Musk", "entity")
        assert result is None

    async def test_no_item_found_returns_none(self) -> None:
        provider = WikidataOntologyProvider()
        provider._search_item = AsyncMock(return_value=None)
        result = await provider.get_ancestry("nonexistent", "concept")
        assert result is None

    async def test_successful_ancestry(self) -> None:
        provider = WikidataOntologyProvider()
        provider._search_item = AsyncMock(return_value="Q5294")
        provider._walk_p279_chain = AsyncMock(return_value=[
            AncestorEntry(name="algorithm", description="a procedure", external_id="Q8366"),
        ])

        result = await provider.get_ancestry("quicksort", "concept")
        assert result is not None
        assert result.source == "wikidata"
        assert len(result.ancestors) == 1


@pytest.mark.asyncio
class TestWikidataIsAvailable:
    async def test_available(self) -> None:
        provider = WikidataOntologyProvider()
        provider._client = AsyncMock()
        provider._client.head = AsyncMock(
            return_value=httpx.Response(200, request=httpx.Request("HEAD", "https://query.wikidata.org/sparql"))
        )
        assert await provider.is_available() is True

    async def test_unavailable_on_error(self) -> None:
        provider = WikidataOntologyProvider()
        provider._client = AsyncMock()
        provider._client.head = AsyncMock(side_effect=httpx.ConnectError("down"))
        assert await provider.is_available() is False
