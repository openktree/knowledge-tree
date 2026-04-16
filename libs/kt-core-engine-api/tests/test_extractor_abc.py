from kt_core_engine_api.extractor import EntityExtractor, ExtractedEntity


class DummyExtractor(EntityExtractor):
    async def extract(self, facts, *, scope=""):
        return [ExtractedEntity(name="x")]


async def test_extract_returns_entities():
    ex = DummyExtractor()
    out = await ex.extract([])
    assert out and out[0].name == "x"


def test_get_last_side_outputs_default_empty():
    assert DummyExtractor().get_last_side_outputs() == {}
