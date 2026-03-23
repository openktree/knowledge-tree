import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

from kt_models.dimensions import (
    _build_fact_prompt,
    _extract_source_names,
    _fact_label,
    _format_fact,
    _parse_dimension_response,
    generate_dimensions,
)


def _make_node(concept: str, attractor: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(concept=concept, attractor=attractor)


def _make_fact(
    content: str,
    fact_type: str = "claim",
    sources: list[SimpleNamespace] | None = None,
    fact_id: uuid.UUID | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=fact_id or uuid.uuid4(),
        content=content,
        fact_type=fact_type,
        sources=sources or [],
    )


def _make_source(
    attribution: str | None = None,
    title: str | None = None,
) -> SimpleNamespace:
    """Create a mock FactSource with optional raw_source."""
    raw_source = SimpleNamespace(title=title) if title else None
    return SimpleNamespace(attribution=attribution, raw_source=raw_source)


class TestExtractSourceNames:
    def test_no_sources(self):
        fact = _make_fact("Some claim")
        assert _extract_source_names(fact) == ""  # type: ignore[arg-type]

    def test_who_from_attribution(self):
        fact = _make_fact("Some claim", sources=[
            _make_source(attribution="who: BBC News; where: bbc.co.uk"),
        ])
        result = _extract_source_names(fact)  # type: ignore[arg-type]
        assert result == " (BBC News)"

    def test_fallback_to_raw_source_title(self):
        fact = _make_fact("Some claim", sources=[
            _make_source(attribution="where: example.com", title="New York Times"),
        ])
        result = _extract_source_names(fact)  # type: ignore[arg-type]
        assert result == " (New York Times)"

    def test_multiple_sources_deduped(self):
        fact = _make_fact("Some claim", sources=[
            _make_source(attribution="who: WHO"),
            _make_source(attribution="who: who"),  # duplicate, different case
            _make_source(attribution="who: IPCC"),
        ])
        result = _extract_source_names(fact)  # type: ignore[arg-type]
        assert result == " (WHO; IPCC)"

    def test_mixed_attribution_and_title(self):
        fact = _make_fact("Some claim", sources=[
            _make_source(attribution="who: Reuters"),
            _make_source(title="The Guardian"),
        ])
        result = _extract_source_names(fact)  # type: ignore[arg-type]
        assert result == " (Reuters; The Guardian)"

    def test_no_attribution_no_title(self):
        fact = _make_fact("Some claim", sources=[
            _make_source(attribution=None, title=None),
        ])
        result = _extract_source_names(fact)  # type: ignore[arg-type]
        assert result == ""

    def test_fact_without_sources_attr(self):
        """Facts without a sources attribute (not loaded) return empty."""
        fact = SimpleNamespace(content="test", fact_type="claim")
        # No 'sources' attribute at all
        result = _extract_source_names(fact)  # type: ignore[arg-type]
        assert result == ""


class TestFactLabel:
    def test_short_content(self):
        assert _fact_label("Hello world") == "Hello world"

    def test_truncates_at_max_words(self):
        content = "one two three four five six seven eight nine"
        label = _fact_label(content)
        assert label == "one two three four five six seven eight…"

    def test_strips_special_chars(self):
        # { and } are stripped; | is replaced with -
        assert _fact_label("fact {with} |pipes|") == "fact with -pipes-"


class TestFormatFact:
    def test_atomic_fact_no_sources(self):
        fact_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        fact = _make_fact("Water boils at 100C", "measurement", fact_id=fact_id)
        result = _format_fact(1, fact)  # type: ignore[arg-type]
        assert result.startswith("  1. [measurement] Water boils at 100C")
        assert "{fact:" + str(fact_id) in result

    def test_atomic_fact_with_sources(self):
        fact_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
        fact = _make_fact("Climate change is real", "claim", sources=[
            _make_source(attribution="who: IPCC"),
        ], fact_id=fact_id)
        result = _format_fact(1, fact)  # type: ignore[arg-type]
        assert "  1. [claim] Climate change is real (IPCC)" in result
        assert "{fact:" + str(fact_id) in result

    def test_compound_fact_with_sources(self):
        fact_id = uuid.UUID("00000000-0000-0000-0000-000000000003")
        fact = _make_fact("Step 1: do this\nStep 2: do that", "procedure", sources=[
            _make_source(attribution="who: NASA"),
        ], fact_id=fact_id)
        result = _format_fact(1, fact)  # type: ignore[arg-type]
        assert "(NASA)" in result
        assert "{fact:" + str(fact_id) in result
        # Compound facts have attribution and tag before the indented content block
        assert result.startswith("  1. [procedure] (NASA)")
        assert "\n    Step 1:" in result

    def test_compound_fact_no_sources(self):
        fact_id = uuid.UUID("00000000-0000-0000-0000-000000000004")
        fact = _make_fact("def hello():\n    pass", "code", fact_id=fact_id)
        result = _format_fact(1, fact)  # type: ignore[arg-type]
        assert result.startswith("  1. [code]")
        assert "{fact:" + str(fact_id) in result
        assert "\n    def hello():" in result


class TestBuildFactPrompt:
    def test_basic_prompt(self):
        node = _make_node("water")
        facts = [_make_fact("Water boils at 100C", "measurement")]
        prompt = _build_fact_prompt(node, facts)  # type: ignore[arg-type]
        assert "water" in prompt
        assert "Water boils at 100C" in prompt
        assert "[measurement]" in prompt

    def test_with_attractor(self):
        node = _make_node("water")
        facts = [_make_fact("Water boils at 100C")]
        prompt = _build_fact_prompt(node, facts, attractor="chemistry")  # type: ignore[arg-type]
        assert "chemistry" in prompt

    def test_multiple_facts(self):
        node = _make_node("water")
        facts = [
            _make_fact("Water boils at 100C"),
            _make_fact("Water freezes at 0C"),
        ]
        prompt = _build_fact_prompt(node, facts)  # type: ignore[arg-type]
        assert "1." in prompt
        assert "2." in prompt

    def test_prompt_includes_source_attribution(self):
        node = _make_node("climate")
        facts = [
            _make_fact("Temperatures are rising", "claim", sources=[
                _make_source(attribution="who: NOAA"),
            ]),
        ]
        prompt = _build_fact_prompt(node, facts)  # type: ignore[arg-type]
        assert "(NOAA)" in prompt


class TestParseDimensionResponse:
    def test_valid_json(self):
        response = json.dumps(
            {
                "content": "Water analysis",
                "confidence": 0.85,
                "suggested_concepts": ["hydrogen", "oxygen"],
            }
        )
        result = _parse_dimension_response(response, "test-model")
        assert result["content"] == "Water analysis"
        assert result["confidence"] == 0.85
        assert result["suggested_concepts"] == ["hydrogen", "oxygen"]
        assert result["relevant_facts"] == []  # not provided → empty

    def test_json_with_code_fence(self):
        response = '```json\n{"content": "Analysis", "confidence": 0.9, "suggested_concepts": []}\n```'
        result = _parse_dimension_response(response, "test-model")
        assert result["content"] == "Analysis"
        assert result["confidence"] == 0.9
        assert result["relevant_facts"] == []

    def test_plain_text_fallback(self):
        response = "This is just plain text analysis without JSON"
        result = _parse_dimension_response(response, "test-model")
        assert result["content"] == response
        assert result["confidence"] == 0.5
        assert result["suggested_concepts"] == []
        assert result["relevant_facts"] == []

    def test_partial_json(self):
        response = '{"content": "Partial"}'
        result = _parse_dimension_response(response, "test-model")
        assert result["content"] == "Partial"
        assert result["confidence"] == 0.5  # default
        assert result["relevant_facts"] == []

    def test_relevant_facts_parsed(self):
        response = json.dumps({
            "content": "Analysis",
            "confidence": 0.8,
            "suggested_concepts": [],
            "relevant_facts": [1, 3, 5],
        })
        result = _parse_dimension_response(response, "test-model")
        assert result["relevant_facts"] == [1, 3, 5]

    def test_relevant_facts_ignores_bad_values(self):
        response = json.dumps({
            "content": "Analysis",
            "confidence": 0.8,
            "suggested_concepts": [],
            "relevant_facts": [1, "bad", None, 3],
        })
        result = _parse_dimension_response(response, "test-model")
        assert result["relevant_facts"] == [1, 3]


class TestGenerateDimensions:
    async def test_empty_facts_returns_empty(self):
        node = _make_node("water")
        gateway = AsyncMock()
        result = await generate_dimensions(node, [], ["model-a"], gateway)  # type: ignore[arg-type]
        assert result == []

    async def test_calls_gateway_parallel(self):
        node = _make_node("water")
        facts = [_make_fact("Water boils at 100C")]

        mock_gateway = AsyncMock()
        mock_gateway.generate_parallel = AsyncMock(
            return_value={
                "model-a": json.dumps({"content": "Analysis A", "confidence": 0.8, "suggested_concepts": ["h2o"], "relevant_facts": [1]}),
                "model-b": json.dumps({"content": "Analysis B", "confidence": 0.7, "suggested_concepts": ["oxygen"], "relevant_facts": [1]}),
            }
        )

        result = await generate_dimensions(
            node,  # type: ignore[arg-type]
            facts,  # type: ignore[arg-type]
            ["model-a", "model-b"],
            mock_gateway,
        )

        assert len(result) == 2
        model_ids = {d["model_id"] for d in result}
        assert "model-a" in model_ids
        assert "model-b" in model_ids
        # relevant_facts should be included in output
        for d in result:
            assert "relevant_facts" in d
            assert d["relevant_facts"] == [1]

    async def test_handles_model_errors(self):
        node = _make_node("water")
        facts = [_make_fact("Water boils at 100C")]

        mock_gateway = AsyncMock()
        mock_gateway.generate_parallel = AsyncMock(
            return_value={
                "model-a": "Error: rate limit exceeded",
                "model-b": json.dumps({"content": "Analysis B", "confidence": 0.7, "suggested_concepts": []}),
            }
        )

        result = await generate_dimensions(
            node,  # type: ignore[arg-type]
            facts,  # type: ignore[arg-type]
            ["model-a", "model-b"],
            mock_gateway,
        )

        # model-a should be skipped due to error
        assert len(result) == 1
        assert result[0]["model_id"] == "model-b"

    async def test_with_attractor(self):
        node = _make_node("water")
        facts = [_make_fact("Water boils at 100C")]

        mock_gateway = AsyncMock()
        mock_gateway.generate_parallel = AsyncMock(
            return_value={
                "model-a": json.dumps({"content": "Chemistry view", "confidence": 0.9, "suggested_concepts": []}),
            }
        )

        result = await generate_dimensions(
            node,  # type: ignore[arg-type]
            facts,  # type: ignore[arg-type]
            ["model-a"],
            mock_gateway,
            attractor="chemistry",
        )

        assert len(result) == 1
        # Verify the gateway was called (attractor is in the prompt, not directly testable here
        # but the call should succeed)
        mock_gateway.generate_parallel.assert_called_once()
