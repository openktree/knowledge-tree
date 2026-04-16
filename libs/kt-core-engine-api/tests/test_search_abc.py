"""Verify KnowledgeProvider ABC + RawSearchResult live in search subpackage."""

from kt_core_engine_api.search import KnowledgeProvider, RawSearchResult


class DummyProvider(KnowledgeProvider):
    @property
    def provider_id(self) -> str:
        return "dummy"

    async def search(self, query, max_results=10):
        return [RawSearchResult(uri="https://x", title="t", raw_content="c", provider_id="dummy")]

    async def is_available(self):
        return True


async def test_dummy_provider_search():
    p = DummyProvider()
    results = await p.search("test")
    assert len(results) == 1
    assert results[0].provider_id == "dummy"


def test_is_public_default():
    assert DummyProvider().is_public is True


async def test_is_available():
    assert await DummyProvider().is_available() is True
