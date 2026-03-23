"""Tests for source-level author extraction strategies."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from kt_facts.author import (
    AuthorInfo,
    LlmHeaderStrategy,
    PdfMetadataStrategy,
    SourceContext,
    _clean_person_field,
    _has_excessive_initials,
    build_author_chain,
    extract_author,
)

# ── PdfMetadataStrategy ────────────────────────────────────────────


class TestPdfMetadataStrategy:
    @pytest.fixture
    def strategy(self) -> PdfMetadataStrategy:
        return PdfMetadataStrategy()

    async def test_extracts_author_and_producer(self, strategy: PdfMetadataStrategy) -> None:
        ctx = SourceContext(
            url="https://example.com/paper.pdf",
            header_text="Some content",
            pdf_metadata={"author": "John Smith", "producer": "Acme Corp", "creator": ""},
        )
        result = await strategy.extract(ctx)
        assert result is not None
        assert result.person == "John Smith"
        assert result.organization == "Acme Corp"

    async def test_returns_none_for_empty_metadata(self, strategy: PdfMetadataStrategy) -> None:
        ctx = SourceContext(
            url="https://example.com/paper.pdf",
            header_text="Some content",
            pdf_metadata={"author": "", "producer": "", "creator": ""},
        )
        result = await strategy.extract(ctx)
        assert result is None

    async def test_returns_none_when_no_pdf_metadata(self, strategy: PdfMetadataStrategy) -> None:
        ctx = SourceContext(url="https://example.com/page", header_text="Some content")
        result = await strategy.extract(ctx)
        assert result is None

    async def test_filters_junk_author_latex(self, strategy: PdfMetadataStrategy) -> None:
        ctx = SourceContext(
            url="https://arxiv.org/paper.pdf",
            header_text="Content",
            pdf_metadata={"author": "", "producer": "pdfTeX-1.40.25", "creator": "LaTeX with hyperref"},
        )
        result = await strategy.extract(ctx)
        assert result is None

    async def test_filters_version_string_producer(self, strategy: PdfMetadataStrategy) -> None:
        ctx = SourceContext(
            url="https://example.com/paper.pdf",
            header_text="Content",
            pdf_metadata={"author": "Jane Doe", "producer": "pdfTeX-1.40.25", "creator": ""},
        )
        result = await strategy.extract(ctx)
        assert result is not None
        assert result.person == "Jane Doe"
        assert result.organization is None

    async def test_falls_back_to_creator(self, strategy: PdfMetadataStrategy) -> None:
        ctx = SourceContext(
            url="https://example.com/paper.pdf",
            header_text="Content",
            pdf_metadata={"author": "", "producer": "", "creator": "Adobe InDesign CC"},
        )
        # Adobe InDesign is in junk list
        result = await strategy.extract(ctx)
        assert result is None

    async def test_real_creator_used(self, strategy: PdfMetadataStrategy) -> None:
        ctx = SourceContext(
            url="https://example.com/paper.pdf",
            header_text="Content",
            pdf_metadata={"author": "", "producer": "", "creator": "Springer Nature"},
        )
        result = await strategy.extract(ctx)
        assert result is not None
        assert result.organization == "Springer Nature"


# ── LlmHeaderStrategy ──────────────────────────────────────────────


class TestLlmHeaderStrategy:
    @pytest.fixture
    def gateway(self) -> MagicMock:
        gw = MagicMock()
        gw.decomposition_model = "test-model"
        gw.decomposition_thinking_level = None
        gw.generate_json = AsyncMock()
        return gw

    async def test_extracts_person_and_org(self, gateway: MagicMock) -> None:
        gateway.generate_json.return_value = {
            "person": "Emma Roth",
            "organization": "The Verge",
        }
        strategy = LlmHeaderStrategy(gateway)
        ctx = SourceContext(
            url="https://www.theverge.com/article",
            header_text="Google appears to be working on...",
        )
        result = await strategy.extract(ctx)
        assert result is not None
        assert result.person == "Emma Roth"
        assert result.organization == "The Verge"

    async def test_handles_null_person(self, gateway: MagicMock) -> None:
        gateway.generate_json.return_value = {
            "person": None,
            "organization": "Wikipedia",
        }
        strategy = LlmHeaderStrategy(gateway)
        ctx = SourceContext(
            url="https://en.wikipedia.org/wiki/Python",
            header_text="Python is a programming language...",
        )
        result = await strategy.extract(ctx)
        assert result is not None
        assert result.person is None
        assert result.organization == "Wikipedia"

    async def test_handles_both_null(self, gateway: MagicMock) -> None:
        gateway.generate_json.return_value = {
            "person": None,
            "organization": None,
        }
        strategy = LlmHeaderStrategy(gateway)
        ctx = SourceContext(url="https://example.com", header_text="Hello world")
        result = await strategy.extract(ctx)
        assert result is None

    async def test_handles_llm_failure(self, gateway: MagicMock) -> None:
        gateway.generate_json.side_effect = RuntimeError("LLM error")
        strategy = LlmHeaderStrategy(gateway)
        ctx = SourceContext(url="https://example.com", header_text="Hello world")
        result = await strategy.extract(ctx)
        assert result is None

    async def test_cleans_n_a_values(self, gateway: MagicMock) -> None:
        gateway.generate_json.return_value = {
            "person": "N/A",
            "organization": "unknown",
        }
        strategy = LlmHeaderStrategy(gateway)
        ctx = SourceContext(url="https://example.com", header_text="Content")
        result = await strategy.extract(ctx)
        assert result is None

    async def test_truncates_header_to_500(self, gateway: MagicMock) -> None:
        gateway.generate_json.return_value = {
            "person": "Author",
            "organization": "Org",
        }
        strategy = LlmHeaderStrategy(gateway)
        long_header = "x" * 1000
        ctx = SourceContext(url="https://example.com", header_text=long_header)
        await strategy.extract(ctx)

        # Check the message sent to LLM has truncated header
        call_args = gateway.generate_json.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        user_content = messages[0]["content"]
        # The header in the prompt should be at most 500 chars
        assert "x" * 501 not in user_content


# ── Chain runner ──────────────────────────────────────────────────


class TestExtractAuthor:
    async def test_returns_first_successful(self) -> None:
        s1 = AsyncMock(spec=PdfMetadataStrategy)
        s1.extract.return_value = None  # Fails
        s2 = AsyncMock(spec=LlmHeaderStrategy)
        s2.extract.return_value = AuthorInfo(person="Jane", organization="BBC")
        ctx = SourceContext(url="https://bbc.com", header_text="Content")

        result = await extract_author([s1, s2], ctx)
        assert result.person == "Jane"
        assert result.organization == "BBC"

    async def test_returns_empty_when_all_fail(self) -> None:
        s1 = AsyncMock()
        s1.extract.return_value = None
        ctx = SourceContext(url="https://example.com", header_text="Content")

        result = await extract_author([s1], ctx)
        assert result.person is None
        assert result.organization is None

    async def test_skips_strategy_with_exception(self) -> None:
        s1 = AsyncMock()
        s1.extract.side_effect = RuntimeError("boom")
        s2 = AsyncMock()
        s2.extract.return_value = AuthorInfo(organization="Fallback Org")
        ctx = SourceContext(url="https://example.com", header_text="Content")

        result = await extract_author([s1, s2], ctx)
        assert result.organization == "Fallback Org"


# ── Hallucination detection ──────────────────────────────────────


class TestHasExcessiveInitials:
    """4+ leading single-letter initials = hallucinated."""

    def test_rejects_four_initials_with_surname(self) -> None:
        assert _has_excessive_initials("A. M. J. M. van der Heijden") is True

    def test_rejects_four_initials_short_surname(self) -> None:
        assert _has_excessive_initials("S. J. M. M. Smith") is True

    def test_allows_three_initials(self) -> None:
        # 3 initials is common in Dutch/European academic names
        assert _has_excessive_initials("M. A. M. van der Heijden") is False
        assert _has_excessive_initials("J. R. R. Tolkien") is False
        assert _has_excessive_initials("G. J. P. van Breukelen") is False

    def test_allows_two_initials(self) -> None:
        assert _has_excessive_initials("J. K. Rowling") is False

    def test_allows_one_initial(self) -> None:
        assert _has_excessive_initials("J. Smith") is False

    def test_allows_no_initials(self) -> None:
        assert _has_excessive_initials("Sarah Chen") is False

    def test_allows_full_name(self) -> None:
        assert _has_excessive_initials("Marco Solmi") is False

    def test_allows_hyphenated(self) -> None:
        assert _has_excessive_initials("Hui-Chuan Hsu") is False

    def test_allows_empty(self) -> None:
        assert _has_excessive_initials("") is False


class TestCleanPersonField:
    def test_filters_four_initial_names(self) -> None:
        person = "A. M. J. M. van der Heijden, S. J. M. M. van der Heijden"
        assert _clean_person_field(person) is None

    def test_keeps_valid_names(self) -> None:
        person = "Sarah Chen, James Rodriguez"
        assert _clean_person_field(person) == "Sarah Chen, James Rodriguez"

    def test_keeps_three_initial_names(self) -> None:
        # 3 initials is legitimate (Dutch/European academic names)
        person = "M. A. M. van der Heijden, J. R. R. Tolkien"
        assert _clean_person_field(person) == "M. A. M. van der Heijden, J. R. R. Tolkien"

    def test_mixed_keeps_only_valid(self) -> None:
        person = "A. M. J. M. van der Heijden, Sarah Chen"
        assert _clean_person_field(person) == "Sarah Chen"

    def test_deduplicates(self) -> None:
        person = "M. J. Schouten, M. J. Schouten, M. J. Schouten"
        assert _clean_person_field(person) == "M. J. Schouten"

    def test_returns_none_for_empty(self) -> None:
        assert _clean_person_field("") is None
        assert _clean_person_field(None) is None

    def test_llm_strategy_filters_hallucinated_names(self) -> None:
        """Integration: LLM returning hallucinated names gets them stripped."""
        gateway = MagicMock()
        gateway.decomposition_model = "test-model"
        gateway.decomposition_thinking_level = None
        gateway.generate_json = AsyncMock(
            return_value={
                "person": "A. M. J. M. van der Heijden, S. J. M. M. van der Heijden",
                "organization": "Nature",
            }
        )
        import asyncio

        strategy = LlmHeaderStrategy(gateway)
        ctx = SourceContext(url="https://nature.com/article", header_text="Abstract...")
        result = asyncio.get_event_loop().run_until_complete(strategy.extract(ctx))
        assert result is not None
        assert result.person is None  # all hallucinated names filtered
        assert result.organization == "Nature"

    def test_pdf_strategy_filters_hallucinated_names(self) -> None:
        """Integration: PDF metadata with hallucinated author names gets filtered."""
        import asyncio

        strategy = PdfMetadataStrategy()
        ctx = SourceContext(
            url="https://example.com/paper.pdf",
            header_text="Content",
            pdf_metadata={
                "author": "A. B. C. D. Smith, Jane Doe",
                "producer": "Springer",
                "creator": "",
            },
        )
        result = asyncio.get_event_loop().run_until_complete(strategy.extract(ctx))
        assert result is not None
        assert result.person == "Jane Doe"  # hallucinated one filtered
        assert result.organization == "Springer"


class TestBuildAuthorChain:
    def test_pdf_chain_has_two_strategies(self) -> None:
        gateway = MagicMock()
        chain = build_author_chain(gateway, is_pdf=True)
        assert len(chain) == 2
        assert isinstance(chain[0], PdfMetadataStrategy)
        assert isinstance(chain[1], LlmHeaderStrategy)

    def test_html_chain_has_one_strategy(self) -> None:
        gateway = MagicMock()
        chain = build_author_chain(gateway, is_pdf=False)
        assert len(chain) == 1
        assert isinstance(chain[0], LlmHeaderStrategy)
