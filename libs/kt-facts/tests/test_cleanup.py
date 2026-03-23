"""Tests for the fact cleanup module."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kt_facts.processing.cleanup import (
    _parse_verdicts,
    cleanup_facts,
)


def _make_fact(content: str) -> SimpleNamespace:
    return SimpleNamespace(content=content)


def _make_gateway(verdicts_response: dict | Exception | None = None) -> AsyncMock:
    """Create a mock ModelGateway that returns the given verdicts from generate_json."""
    gw = AsyncMock()
    gw.decomposition_model = "test-model"
    gw.decomposition_thinking_level = ""
    if isinstance(verdicts_response, Exception):
        gw.generate_json = AsyncMock(side_effect=verdicts_response)
    elif verdicts_response is not None:
        gw.generate_json = AsyncMock(return_value=verdicts_response)
    else:
        gw.generate_json = AsyncMock(return_value={"verdicts": []})
    return gw


class TestParseVerdicts:
    def test_all_kept(self) -> None:
        data = {
            "verdicts": [
                {"index": 0, "keep": True},
                {"index": 1, "keep": True},
            ]
        }
        result = _parse_verdicts(data, 2)
        assert result == [(True, ""), (True, "")]

    def test_some_rejected(self) -> None:
        data = {
            "verdicts": [
                {"index": 0, "keep": True},
                {"index": 1, "keep": False, "reason": "bare heading"},
            ]
        }
        result = _parse_verdicts(data, 2)
        assert result == [(True, ""), (False, "bare heading")]

    def test_missing_index_defaults_to_keep(self) -> None:
        data = {
            "verdicts": [
                {"index": 0, "keep": False, "reason": "noise"},
            ]
        }
        result = _parse_verdicts(data, 3)
        assert result[0] == (False, "noise")
        assert result[1] == (True, "")  # missing → kept
        assert result[2] == (True, "")  # missing → kept

    def test_non_list_verdicts_returns_all_keep(self) -> None:
        data = {"verdicts": "not a list"}
        result = _parse_verdicts(data, 2)
        assert result == [(True, ""), (True, "")]

    def test_empty_data_returns_all_keep(self) -> None:
        data = {}
        result = _parse_verdicts(data, 2)
        assert result == [(True, ""), (True, "")]

    def test_malformed_entry_skipped(self) -> None:
        data = {
            "verdicts": [
                {"index": 0, "keep": True},
                "not a dict",
                {"no_index": True, "keep": False},
                {"index": 1, "keep": False, "reason": "bad"},
            ]
        }
        result = _parse_verdicts(data, 2)
        assert result == [(True, ""), (False, "bad")]

    def test_non_bool_keep_defaults_to_true(self) -> None:
        data = {
            "verdicts": [
                {"index": 0, "keep": "yes"},
            ]
        }
        result = _parse_verdicts(data, 1)
        assert result == [(True, "")]

    def test_non_string_reason_converted(self) -> None:
        data = {
            "verdicts": [
                {"index": 0, "keep": False, "reason": 42},
            ]
        }
        result = _parse_verdicts(data, 1)
        assert result == [(False, "42")]


class TestCleanupFacts:
    @pytest.mark.asyncio
    async def test_all_long_facts_pass_through(self) -> None:
        """Facts at or above min_words are never sent to LLM."""
        facts = [
            _make_fact("This is a sufficiently long fact with many words in it"),
            _make_fact("Another long fact that easily passes the word count threshold"),
        ]
        gw = _make_gateway()
        result = await cleanup_facts(facts, min_words=5, gateway=gw)

        assert len(result.kept) == 2
        assert len(result.rejected) == 0
        gw.generate_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_short_facts_evaluated(self) -> None:
        """Short facts are sent to LLM for validation."""
        facts = [
            _make_fact("Too short"),
            _make_fact("Also short"),
            _make_fact("This one is long enough to pass the minimum word count easily"),
        ]
        gw = _make_gateway(
            {
                "verdicts": [
                    {"index": 0, "keep": True},
                    {"index": 1, "keep": False, "reason": "bare heading"},
                ]
            }
        )

        result = await cleanup_facts(facts, min_words=8, gateway=gw)

        assert len(result.kept) == 2  # 1 long + 1 short kept
        assert len(result.rejected) == 1
        assert result.rejected[0].content == "Also short"
        assert result.rejected[0].reason == "bare heading"

    @pytest.mark.asyncio
    async def test_empty_facts_list(self) -> None:
        """Empty input returns empty result without LLM call."""
        gw = _make_gateway()
        result = await cleanup_facts([], min_words=5, gateway=gw)

        assert len(result.kept) == 0
        assert len(result.rejected) == 0
        gw.generate_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_fail_open_on_llm_error(self) -> None:
        """If LLM call raises, all facts are kept (fail-open)."""
        facts = [_make_fact("Short")]
        gw = _make_gateway(RuntimeError("API down"))

        result = await cleanup_facts(facts, min_words=5, gateway=gw)

        assert len(result.kept) == 1
        assert len(result.rejected) == 0

    @pytest.mark.asyncio
    async def test_fail_open_on_malformed_response(self) -> None:
        """If LLM returns garbage, all short facts are kept."""
        facts = [_make_fact("Short"), _make_fact("Also short")]
        gw = _make_gateway({"not_verdicts": True})

        result = await cleanup_facts(facts, min_words=10, gateway=gw)

        assert len(result.kept) == 2
        assert len(result.rejected) == 0

    @pytest.mark.asyncio
    async def test_batching(self) -> None:
        """Short facts are split into batches of the configured size."""
        facts = [_make_fact(f"Fact {i}") for i in range(5)]
        call_count = 0

        async def mock_generate_json(**kwargs) -> dict:
            nonlocal call_count
            call_count += 1
            # Keep all in each batch
            msg_content = kwargs.get("messages", [{}])[0].get("content", "")
            # Count how many candidates are in this batch
            lines = [l for l in msg_content.split("\n") if l and l[0].isdigit()]
            return {"verdicts": [{"index": i, "keep": True} for i in range(len(lines))]}

        gw = _make_gateway()
        gw.generate_json = AsyncMock(side_effect=mock_generate_json)

        result = await cleanup_facts(facts, min_words=10, gateway=gw, batch_size=2)

        assert len(result.kept) == 5
        assert gw.generate_json.call_count == 3  # 2 + 2 + 1

    @pytest.mark.asyncio
    async def test_custom_content_accessor(self) -> None:
        """content_accessor is used to extract text from fact objects."""
        facts = [{"text": "Short"}, {"text": "This is a long fact with enough words"}]
        gw = _make_gateway({"verdicts": [{"index": 0, "keep": False, "reason": "too short"}]})

        result = await cleanup_facts(
            facts,
            min_words=5,
            gateway=gw,
            content_accessor=lambda f: f["text"],
        )

        assert len(result.kept) == 1  # long one
        assert len(result.rejected) == 1
        assert result.rejected[0].content == "Short"

    @pytest.mark.asyncio
    async def test_progress_callback(self) -> None:
        """Progress callback is invoked for cleanup phase."""
        facts = [_make_fact("Short"), _make_fact("Also short")]
        gw = _make_gateway(
            {
                "verdicts": [
                    {"index": 0, "keep": True},
                    {"index": 1, "keep": True},
                ]
            }
        )
        progress_calls: list[tuple[str, int, int]] = []

        async def on_progress(phase: str, processed: int, total: int) -> None:
            progress_calls.append((phase, processed, total))

        await cleanup_facts(
            facts,
            min_words=10,
            gateway=gw,
            on_progress=on_progress,
        )

        assert len(progress_calls) >= 1
        assert progress_calls[0][0] == "cleanup"
        # Final call should show all processed
        assert progress_calls[-1][1] == 2
        assert progress_calls[-1][2] == 2

    @pytest.mark.asyncio
    async def test_word_count_boundary(self) -> None:
        """A fact with exactly min_words is NOT sent for cleanup (>= passes)."""
        # "one two three four five" = 5 words
        facts = [_make_fact("one two three four five")]
        gw = _make_gateway()

        result = await cleanup_facts(facts, min_words=5, gateway=gw)

        assert len(result.kept) == 1
        gw.generate_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_short_facts_skips_llm(self) -> None:
        """When all facts are long enough, no LLM call is made."""
        facts = [
            _make_fact("This sentence has more than five words in it"),
            _make_fact("And this sentence also has more than five words"),
        ]
        gw = _make_gateway()

        result = await cleanup_facts(facts, min_words=5, gateway=gw)

        assert len(result.kept) == 2
        assert len(result.rejected) == 0
        gw.generate_json.assert_not_called()
