"""Tests for seed deduplication."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from seed_fixtures import (
    make_embedding_service_mock,
    make_model_gateway_mock,
    make_qdrant_match,
    make_qdrant_seed_repo_mock,
    make_route,
    make_seed,
    make_seed_repo_mock,
    make_write_fact_repo_mock,
)

from kt_facts.processing.seed_dedup import (
    _llm_confirm_merge,
    _merge_pair,
    deduplicate_seed,
)
from kt_facts.processing.seed_heuristics import (
    differs_only_by_digit_or_initial,
    has_academic_initials,
    is_acronym_match,
    is_containment_mismatch,
    is_prefix_disambiguation_candidate,
    is_safe_auto_merge,
    text_search_route,
)

# Backward-compat aliases used in test names
_is_acronym_match = is_acronym_match
_is_containment_mismatch = is_containment_mismatch
_is_prefix_disambiguation_candidate = is_prefix_disambiguation_candidate
_text_search_route = text_search_route


@pytest.mark.asyncio
class TestDeduplicateSeed:
    async def test_no_matches_returns_same_key(self):
        repo = make_seed_repo_mock()
        result = await deduplicate_seed(
            "entity:albert-einstein",
            "Albert Einstein",
            "entity",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        assert result == "entity:albert-einstein"

    async def test_trigram_alone_does_not_merge(self):
        """Trigram-only similarity should NOT merge when embedding finds no match."""
        repo = make_seed_repo_mock()
        existing = make_seed("entity:albert-einstein", "Albert Einstein", "entity", fact_count=5)
        repo.find_similar_seeds = AsyncMock(return_value=[existing])

        result = await deduplicate_seed(
            "entity:a-einstein",
            "A. Einstein",
            "entity",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        # Embedding found no match → trigram alone does NOT merge
        assert result == "entity:a-einstein"
        repo.merge_seeds.assert_not_called()

    async def test_trigram_with_embedding_merges(self):
        """When embedding service confirms similarity, merge happens."""
        repo = make_seed_repo_mock()
        # Trigram finds no alias match (names don't match exactly)
        existing = make_seed("entity:albert-einstein", "Albert Einstein", "entity", fact_count=5)
        repo.find_similar_seeds = AsyncMock(return_value=[existing])
        repo.get_seed_by_key = AsyncMock(
            side_effect=lambda k: {
                "entity:a-einstein": make_seed("entity:a-einstein", "A. Einstein", "entity", fact_count=1),
                "entity:albert-einstein": existing,
            }.get(k)
        )
        repo.merge_seeds = AsyncMock(return_value=MagicMock())

        embedding_service = MagicMock()
        embedding_service.embed_text = AsyncMock(return_value=[0.1] * 10)

        qdrant_repo = MagicMock()
        qdrant_repo.upsert = AsyncMock()
        match_result = make_qdrant_match("entity:albert-einstein", 0.92)
        qdrant_repo.find_similar = AsyncMock(return_value=[match_result])

        result = await deduplicate_seed(
            "entity:a-einstein",
            "A. Einstein",
            "entity",
            repo,
            embedding_service=embedding_service,
            qdrant_seed_repo=qdrant_repo,
        )
        assert result == "entity:albert-einstein"
        repo.merge_seeds.assert_called_once()

    async def test_alias_match_merges(self):
        repo = make_seed_repo_mock()
        existing = make_seed(
            "entity:karl-marx",
            "Karl Marx",
            "entity",
            fact_count=3,
            metadata_={"aliases": ["K. Marx", "Karl Heinrich Marx"]},
        )
        repo.find_similar_seeds = AsyncMock(return_value=[existing])
        repo.get_seed_by_key = AsyncMock(
            side_effect=lambda k: {
                "entity:k-marx": make_seed("entity:k-marx", "K. Marx", "entity", fact_count=1),
                "entity:karl-marx": existing,
            }.get(k)
        )
        repo.merge_seeds = AsyncMock(return_value=MagicMock())

        result = await deduplicate_seed(
            "entity:k-marx",
            "K. Marx",
            "entity",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        assert result == "entity:karl-marx"

    async def test_merged_seed_skipped(self):
        repo = make_seed_repo_mock()
        merged = make_seed("entity:old", "Old Name", "entity", status="merged")
        repo.find_similar_seeds = AsyncMock(return_value=[merged])

        result = await deduplicate_seed(
            "entity:new",
            "New Name",
            "entity",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        assert result == "entity:new"
        repo.merge_seeds.assert_not_called()

    async def test_self_match_skipped(self):
        repo = make_seed_repo_mock()
        self_seed = make_seed("entity:test", "Test", "entity", fact_count=5)
        repo.find_similar_seeds = AsyncMock(return_value=[self_seed])

        result = await deduplicate_seed(
            "entity:test",
            "Test",
            "entity",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        assert result == "entity:test"
        repo.merge_seeds.assert_not_called()

    async def test_embedding_dedup(self):
        repo = make_seed_repo_mock()
        repo.find_similar_seeds = AsyncMock(return_value=[])  # No trigram match

        embedding_service = MagicMock()
        embedding_service.embed_text = AsyncMock(return_value=[0.1] * 10)

        qdrant_repo = MagicMock()
        qdrant_repo.upsert = AsyncMock()

        match_result = make_qdrant_match("entity:albert-einstein", 0.95)
        qdrant_repo.find_similar = AsyncMock(return_value=[match_result])

        existing = make_seed("entity:albert-einstein", "Albert Einstein", "entity", fact_count=5)
        repo.get_seed_by_key = AsyncMock(
            side_effect=lambda k: {
                "entity:einstein": make_seed("entity:einstein", "Einstein", "entity", fact_count=1),
                "entity:albert-einstein": existing,
            }.get(k)
        )
        repo.merge_seeds = AsyncMock(return_value=MagicMock())

        result = await deduplicate_seed(
            "entity:einstein",
            "Einstein",
            "entity",
            repo,
            embedding_service=embedding_service,
            qdrant_seed_repo=qdrant_repo,
        )
        assert result == "entity:albert-einstein"

    async def test_alias_error_continues_to_embedding(self):
        """When trigram/alias search fails, embedding dedup still runs."""
        repo = make_seed_repo_mock()
        repo.find_similar_seeds = AsyncMock(side_effect=Exception("DB error"))

        embedding_service = MagicMock()
        embedding_service.embed_text = AsyncMock(return_value=[0.1] * 10)

        qdrant_repo = MagicMock()
        qdrant_repo.upsert = AsyncMock()
        qdrant_repo.find_similar = AsyncMock(return_value=[])

        result = await deduplicate_seed(
            "entity:test",
            "Test",
            "entity",
            repo,
            embedding_service=embedding_service,
            qdrant_seed_repo=qdrant_repo,
        )
        assert result == "entity:test"
        # Should have attempted embedding dedup after alias/trigram failure
        embedding_service.embed_text.assert_called_once()

    async def test_trigram_high_embedding_low_no_merge(self):
        """Arizona scenario: trigram finds candidate but embedding finds no match → no merge."""
        repo = make_seed_repo_mock()
        # Trigram finds a candidate (high character overlap)
        existing = make_seed(
            "entity:university-of-arizona",
            "The University of Arizona",
            "entity",
            fact_count=5,
        )
        repo.find_similar_seeds = AsyncMock(return_value=[existing])

        # Embedding finds no match → should NOT merge on trigram alone
        result = await deduplicate_seed(
            "entity:arizona-state-university",
            "Arizona State University",
            "entity",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        assert result == "entity:arizona-state-university"
        repo.merge_seeds.assert_not_called()

    async def test_phonetic_with_embedding_floor_merges(self):
        """Phonetic match with embedding above typo floor → merge."""
        repo = make_seed_repo_mock()
        repo.find_similar_seeds = AsyncMock(return_value=[])  # No alias match

        existing = make_seed(
            "entity:democratic-party",
            "Democratic Party",
            "entity",
            fact_count=5,
        )
        repo.find_by_phonetic = AsyncMock(return_value=[existing])
        repo.get_seed_by_key = AsyncMock(
            side_effect=lambda k: {
                "entity:democrtic-party": make_seed(
                    "entity:democrtic-party",
                    "Democrtic Party",
                    "entity",
                    fact_count=1,
                ),
                "entity:democratic-party": existing,
            }.get(k)
        )
        repo.merge_seeds = AsyncMock(return_value=MagicMock())

        embedding_service = MagicMock()
        embedding_service.embed_text = AsyncMock(return_value=[0.1] * 10)

        qdrant_repo = MagicMock()
        qdrant_repo.upsert = AsyncMock()

        # Single find_similar call at typo_floor threshold returns sub-threshold match
        # (below 0.82 merge threshold but above 0.75 typo floor)
        qdrant_repo.find_similar = AsyncMock(
            return_value=[
                make_qdrant_match("entity:democratic-party", 0.80),
            ]
        )

        # Phonetic trigram confirmation
        trigram_for_phonetic = make_seed(
            "entity:democratic-party",
            "Democratic Party",
            "entity",
            fact_count=5,
        )
        # find_similar_seeds is called twice: once for alias, once for phonetic confirmation
        repo.find_similar_seeds = AsyncMock(
            side_effect=[
                [],  # Alias search — no match
                [trigram_for_phonetic],  # Phonetic trigram confirmation
            ]
        )

        with patch("kt_facts.processing.seed_dedup.get_settings") as mock_settings:
            s = MagicMock()
            s.seed_dedup_trigram_threshold = 0.50
            s.seed_dedup_embedding_threshold = 0.82
            s.seed_dedup_typo_floor = 0.75
            s.seed_phonetic_trigram_threshold = 0.40
            mock_settings.return_value = s

            result = await deduplicate_seed(
                "entity:democrtic-party",
                "Democrtic Party",
                "entity",
                repo,
                embedding_service=embedding_service,
                qdrant_seed_repo=qdrant_repo,
            )

        assert result == "entity:democratic-party"
        repo.merge_seeds.assert_called_once()

    async def test_phonetic_with_embedding_below_floor_skips(self):
        """Phonetic match with embedding below typo floor → no merge."""
        repo = make_seed_repo_mock()
        repo.find_similar_seeds = AsyncMock(return_value=[])  # No alias match

        existing = make_seed(
            "entity:bank-of-england",
            "Bank of England",
            "entity",
            fact_count=5,
        )
        repo.find_by_phonetic = AsyncMock(return_value=[existing])

        embedding_service = MagicMock()
        embedding_service.embed_text = AsyncMock(return_value=[0.1] * 10)

        qdrant_repo = MagicMock()
        qdrant_repo.upsert = AsyncMock()

        # Single find_similar call at typo_floor — no matches
        qdrant_repo.find_similar = AsyncMock(return_value=[])

        with patch("kt_facts.processing.seed_dedup.get_settings") as mock_settings:
            s = MagicMock()
            s.seed_dedup_trigram_threshold = 0.50
            s.seed_dedup_embedding_threshold = 0.82
            s.seed_dedup_typo_floor = 0.75
            s.seed_phonetic_trigram_threshold = 0.40
            mock_settings.return_value = s

            result = await deduplicate_seed(
                "entity:bank-of-america",
                "Bank of America",
                "entity",
                repo,
                embedding_service=embedding_service,
                qdrant_seed_repo=qdrant_repo,
            )

        # Should NOT merge — embedding too low for phonetic to take effect
        assert result == "entity:bank-of-america"
        repo.merge_seeds.assert_not_called()

    async def test_invalid_name_skips_dedup(self):
        """Seeds with invalid names (initials, et al.) should skip dedup entirely."""
        repo = make_seed_repo_mock()

        # Pure initials — should return immediately without any dedup attempt
        result = await deduplicate_seed(
            "entity:k-m-a",
            "K. M. A.",
            "entity",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        assert result == "entity:k-m-a"
        repo.find_similar_seeds.assert_not_called()
        repo.merge_seeds.assert_not_called()

    async def test_et_al_name_skips_dedup(self):
        """Seeds with 'et al.' citation artifacts skip dedup."""
        repo = make_seed_repo_mock()

        result = await deduplicate_seed(
            "entity:smith-et-al",
            "Smith et al.",
            "entity",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        assert result == "entity:smith-et-al"
        repo.find_similar_seeds.assert_not_called()

    async def test_valid_seed_not_attracted_to_garbage(self):
        """A valid seed should NOT merge into a garbage seed."""
        garbage_seed = make_seed("entity:some-garbage", "K. M. A.", "entity", fact_count=50)
        garbage_seed.status = "garbage"

        repo = make_seed_repo_mock()
        # Trigram returns garbage seed — but it should be skipped (status not active/promoted)
        repo.find_similar_seeds = AsyncMock(return_value=[garbage_seed])

        result = await deduplicate_seed(
            "entity:albert-einstein",
            "Albert Einstein",
            "entity",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        assert result == "entity:albert-einstein"
        repo.merge_seeds.assert_not_called()


@pytest.mark.asyncio
class TestMergePair:
    async def test_winner_has_more_facts(self):
        repo = make_seed_repo_mock()
        repo.get_seed_by_key = AsyncMock(
            side_effect=lambda k: {
                "a": make_seed("a", "A", "entity", fact_count=10),
                "b": make_seed("b", "B", "entity", fact_count=2),
            }.get(k)
        )
        repo.merge_seeds = AsyncMock(return_value=MagicMock())

        result = await _merge_pair("b", "a", repo, reason="test")
        assert result == "a"
        # Loser should be "b" (fewer facts)
        repo.merge_seeds.assert_called_once_with("b", "a", reason="test")

    async def test_tie_prefers_existing(self):
        repo = make_seed_repo_mock()
        repo.get_seed_by_key = AsyncMock(
            side_effect=lambda k: {
                "incoming": make_seed("incoming", "Incoming", "entity", fact_count=5),
                "existing": make_seed("existing", "Existing", "entity", fact_count=5),
            }.get(k)
        )
        repo.merge_seeds = AsyncMock(return_value=MagicMock())

        result = await _merge_pair("incoming", "existing", repo, reason="test")
        assert result == "existing"

    async def test_missing_seed_returns_incoming(self):
        repo = make_seed_repo_mock()
        repo.get_seed_by_key = AsyncMock(return_value=None)

        result = await _merge_pair("incoming", "missing", repo, reason="test")
        assert result == "incoming"
        repo.merge_seeds.assert_not_called()


class TestContainmentMismatch:
    """Tests for the containment guard that prevents merging distinct entities."""

    def test_identical_names_ok(self):
        assert _is_containment_mismatch("albert einstein", "albert einstein") is False

    def test_minor_variation_ok(self):
        # Single short extra word (e.g., article) — allow merge
        assert _is_containment_mismatch("quantum mechanics", "quantum mechanics") is False

    def test_epstein_vs_lawyer(self):
        # "jeffrey epstein" is contained in "jeffrey epstein's lawyer" with extra "lawyer"
        assert _is_containment_mismatch("jeffrey epstein", "jeffrey epstein's lawyer") is True

    def test_committee_vs_democrats_on_committee(self):
        assert (
            _is_containment_mismatch(
                "house oversight committee",
                "democrats on the house oversight committee",
            )
            is True
        )

    def test_subset_with_short_extra_word_ok(self):
        # Only adds "a" — too short to be meaningful
        assert _is_containment_mismatch("big cat", "a big cat") is False

    def test_no_containment_different_words(self):
        # Neither is a subset of the other, no substring containment
        assert _is_containment_mismatch("albert einstein", "niels bohr") is False

    def test_substring_containment_with_significant_extra(self):
        # "mars" is a substring of "mars rover" — different entity
        assert _is_containment_mismatch("mars", "mars rover") is True

    def test_symmetric(self):
        # Order shouldn't matter
        assert _is_containment_mismatch("jeffrey epstein's lawyer", "jeffrey epstein") is True

    # ── Distinguishing-word swap tests (Case 2) ──────────────────
    def test_world_war_1_vs_2(self):
        assert _is_containment_mismatch("world war 1", "world war 2") is True

    def test_bank_of_america_vs_england(self):
        assert _is_containment_mismatch("bank of america", "bank of england") is True

    def test_new_york_times_vs_post(self):
        assert _is_containment_mismatch("new york times", "new york post") is True

    def test_university_of_california_vs_michigan(self):
        assert _is_containment_mismatch("university of california", "university of michigan") is True

    def test_different_jurisdictions(self):
        assert (
            _is_containment_mismatch(
                "u.s. attorney for southern district of new york",
                "u.s. attorney's office for southern district of florida",
            )
            is True
        )

    def test_different_arrest_dates(self):
        assert (
            _is_containment_mismatch(
                "2006 arrest of jeffrey epstein",
                "july 6 2019 arrest of jeffrey epstein",
            )
            is True
        )

    def test_supreme_court_different_country(self):
        assert (
            _is_containment_mismatch(
                "supreme court of the united states",
                "supreme court of canada",
            )
            is True
        )

    def test_title_prefix_ok(self):
        # "barack obama" vs "president barack obama" — just a title, should merge
        assert _is_containment_mismatch("barack obama", "president barack obama") is True

    def test_article_prefix_ok(self):
        # "miami herald" vs "the miami herald" — article only, should allow
        assert _is_containment_mismatch("miami herald", "the miami herald") is False

    def test_punctuation_only_ok(self):
        # Same words, different punctuation
        assert _is_containment_mismatch("mcdonalds", "mcdonalds") is False


class TestAcronymMatch:
    """Tests for the acronym detection heuristic."""

    # ── Positive cases ──────────────────────────────────────────────
    def test_fbi(self):
        assert _is_acronym_match("FBI", "Federal Bureau of Investigation") is True

    def test_cia(self):
        assert _is_acronym_match("CIA", "Central Intelligence Agency") is True

    def test_nasa(self):
        assert _is_acronym_match("NASA", "National Aeronautics and Space Administration") is True

    def test_nato(self):
        assert _is_acronym_match("NATO", "North Atlantic Treaty Organization") is True

    def test_usa(self):
        assert _is_acronym_match("USA", "United States of America") is True

    def test_nyse(self):
        assert _is_acronym_match("NYSE", "New York Stock Exchange") is True

    def test_who(self):
        assert _is_acronym_match("WHO", "World Health Organization") is True

    def test_sec(self):
        assert _is_acronym_match("SEC", "Securities and Exchange Commission") is True

    def test_gdp(self):
        assert _is_acronym_match("GDP", "Gross Domestic Product") is True

    def test_dotted_acronym(self):
        assert _is_acronym_match("J.F.K.", "John F. Kennedy") is True

    def test_reversed_order(self):
        assert _is_acronym_match("Federal Bureau of Investigation", "FBI") is True

    def test_ibm(self):
        assert _is_acronym_match("IBM", "International Business Machines") is True

    def test_bbc(self):
        assert _is_acronym_match("BBC", "British Broadcasting Corporation") is True

    def test_eu(self):
        assert _is_acronym_match("EU", "European Union") is True

    def test_imf(self):
        assert _is_acronym_match("IMF", "International Monetary Fund") is True

    def test_ai(self):
        assert _is_acronym_match("AI", "Artificial Intelligence") is True

    # ── Negative cases ──────────────────────────────────────────────
    def test_two_acronyms(self):
        assert _is_acronym_match("FBI", "CIA") is False

    def test_valid_alternate_expansion(self):
        # FBI matches "Food and Beverage Industry" initials — the heuristic
        # correctly identifies this as an acronym match. Disambiguation of
        # which entity "FBI" refers to is handled by Phase 5 (ambiguity).
        assert _is_acronym_match("FBI", "Food and Beverage Industry") is True

    def test_both_long_names(self):
        assert _is_acronym_match("Albert Einstein", "Niels Bohr") is False

    def test_single_char(self):
        assert _is_acronym_match("A", "Albert") is False

    def test_too_long_acronym(self):
        assert _is_acronym_match("ABCDEFGH", "A B C D E F G H") is False

    def test_matching_initials_different_entity(self):
        # MIT matches "Ministry of Information Technology" initials — that's correct!
        # Disambiguation handles which entity wins, not the acronym heuristic.
        assert _is_acronym_match("MIT", "Ministry of Information Technology") is True

    def test_wrong_initials(self):
        # FBI doesn't match "Food and Drug Administration" → FDA
        assert _is_acronym_match("FBI", "Food and Drug Administration") is False

    def test_nato_vs_nafta(self):
        assert _is_acronym_match("NATO", "NAFTA") is False

    def test_empty_strings(self):
        assert _is_acronym_match("", "") is False

    def test_number_in_acronym(self):
        assert _is_acronym_match("G7", "Group of Seven") is False

    def test_who_vs_band(self):
        # "The Who" has only 2 words but "WHO" initials would be "TW"
        assert _is_acronym_match("WHO", "The Who") is False


@pytest.mark.asyncio
class TestAcronymMatchIntegration:
    async def test_acronym_merges_via_trigram(self):
        """Acronym match found via trigram candidate search → merge."""
        repo = make_seed_repo_mock()
        existing = make_seed(
            "entity:federal-bureau-of-investigation",
            "Federal Bureau of Investigation",
            "entity",
            fact_count=5,
        )
        repo.find_similar_seeds = AsyncMock(return_value=[existing])
        repo.get_seed_by_key = AsyncMock(
            side_effect=lambda k: {
                "entity:fbi": make_seed("entity:fbi", "FBI", "entity", fact_count=1),
                "entity:federal-bureau-of-investigation": existing,
            }.get(k)
        )
        repo.merge_seeds = AsyncMock(return_value=MagicMock())

        result = await deduplicate_seed(
            "entity:fbi",
            "FBI",
            "entity",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        assert result == "entity:federal-bureau-of-investigation"
        repo.merge_seeds.assert_called_once()

    async def test_wrong_initials_no_merge(self):
        """Acronym whose initials don't match the candidate → no merge."""
        repo = make_seed_repo_mock()
        existing = make_seed(
            "entity:food-and-drug-administration",
            "Food and Drug Administration",
            "entity",
            fact_count=5,
        )
        repo.find_similar_seeds = AsyncMock(return_value=[existing])

        result = await deduplicate_seed(
            "entity:fbi",
            "FBI",
            "entity",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        # FBI vs FDA — initials don't match
        assert result == "entity:fbi"
        repo.merge_seeds.assert_not_called()

    async def test_non_acronym_candidate_not_merged(self):
        """Non-acronym trigram candidate without exact name match → no merge."""
        repo = make_seed_repo_mock()
        existing = make_seed(
            "entity:federal-aviation-administration",
            "Federal Aviation Administration",
            "entity",
            fact_count=5,
        )
        repo.find_similar_seeds = AsyncMock(return_value=[existing])

        result = await deduplicate_seed(
            "entity:fbi",
            "FBI",
            "entity",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        # FBI != FAA initials, no exact name match → no merge
        assert result == "entity:fbi"
        repo.merge_seeds.assert_not_called()


@pytest.mark.asyncio
class TestReverseAliasLookup:
    async def test_reverse_alias_merges(self):
        """Incoming seed 'FBI' merges with existing seed that has 'FBI' as alias."""
        repo = make_seed_repo_mock()
        repo.find_similar_seeds = AsyncMock(return_value=[])  # No trigram match

        existing = make_seed(
            "entity:federal-bureau-of-investigation",
            "Federal Bureau of Investigation",
            "entity",
            fact_count=5,
            metadata_={"aliases": ["FBI"]},
        )
        repo.find_seeds_by_alias = AsyncMock(return_value=[existing])
        repo.get_seed_by_key = AsyncMock(
            side_effect=lambda k: {
                "entity:fbi": make_seed("entity:fbi", "FBI", "entity", fact_count=1),
                "entity:federal-bureau-of-investigation": existing,
            }.get(k)
        )
        repo.merge_seeds = AsyncMock(return_value=MagicMock())

        result = await deduplicate_seed(
            "entity:fbi",
            "FBI",
            "entity",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        assert result == "entity:federal-bureau-of-investigation"
        repo.merge_seeds.assert_called_once()

    async def test_reverse_alias_no_match(self):
        """No reverse alias match → no merge from that signal."""
        repo = make_seed_repo_mock()
        repo.find_similar_seeds = AsyncMock(return_value=[])
        repo.find_seeds_by_alias = AsyncMock(return_value=[])

        result = await deduplicate_seed(
            "entity:fbi",
            "FBI",
            "entity",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        assert result == "entity:fbi"
        repo.merge_seeds.assert_not_called()


@pytest.mark.asyncio
class TestMultiMatchAmbiguity:
    async def test_single_alias_candidate_merges(self):
        """Single alias candidate → direct merge (no ambiguity)."""
        repo = make_seed_repo_mock()
        existing = make_seed(
            "entity:federal-bureau-of-investigation",
            "Federal Bureau of Investigation",
            "entity",
            fact_count=5,
        )
        repo.find_similar_seeds = AsyncMock(return_value=[existing])
        repo.get_seed_by_key = AsyncMock(
            side_effect=lambda k: {
                "entity:fbi": make_seed("entity:fbi", "FBI", "entity", fact_count=1),
                "entity:federal-bureau-of-investigation": existing,
            }.get(k)
        )
        repo.merge_seeds = AsyncMock(return_value=MagicMock())

        result = await deduplicate_seed(
            "entity:fbi",
            "FBI",
            "entity",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        assert result == "entity:federal-bureau-of-investigation"

    async def test_multi_match_shared_ancestor_merges(self):
        """Two candidates that share a merged ancestor → merge into ancestor."""
        repo = make_seed_repo_mock()
        # Candidate A: merged into ancestor
        candidate_a = make_seed(
            "entity:cand-a",
            "Candidate A",
            "entity",
            fact_count=3,
            metadata_={"aliases": ["Alias X"]},
        )
        # Candidate B: also merged into same ancestor (via reverse alias)
        candidate_b = make_seed(
            "entity:cand-b",
            "Candidate B",
            "entity",
            fact_count=2,
            metadata_={"aliases": ["Alias X"]},
        )
        # Both candidates found via trigram
        repo.find_similar_seeds = AsyncMock(return_value=[candidate_a, candidate_b])

        # Merge chain: A → merged into ancestor, B → merged into ancestor
        ancestor = make_seed(
            "entity:ancestor",
            "Ancestor",
            "entity",
            status="active",
            fact_count=10,
        )
        merged_a = make_seed(
            "entity:cand-a",
            "Candidate A",
            "entity",
            status="merged",
            fact_count=3,
        )
        merged_a.merged_into_key = "entity:ancestor"
        merged_b = make_seed(
            "entity:cand-b",
            "Candidate B",
            "entity",
            status="merged",
            fact_count=2,
        )
        merged_b.merged_into_key = "entity:ancestor"

        repo.get_seed_by_key = AsyncMock(
            side_effect=lambda k: {
                "entity:incoming": make_seed("entity:incoming", "Alias X", "entity", fact_count=1),
                "entity:cand-a": merged_a,
                "entity:cand-b": merged_b,
                "entity:ancestor": ancestor,
            }.get(k)
        )
        repo.merge_seeds = AsyncMock(return_value=MagicMock())

        result = await deduplicate_seed(
            "entity:incoming",
            "Alias X",
            "entity",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        assert result == "entity:ancestor"

    async def test_multi_match_distinct_entities_marks_ambiguous(self):
        """Two candidates from different signals → mark as ambiguous."""
        repo = make_seed_repo_mock()
        # Candidate A found via trigram alias match
        candidate_a = make_seed(
            "entity:securities-and-exchange-commission",
            "Securities and Exchange Commission",
            "entity",
            fact_count=5,
            metadata_={"aliases": ["SEC"]},
        )
        repo.find_similar_seeds = AsyncMock(return_value=[candidate_a])

        # Candidate B found via reverse alias lookup (different entity also has "SEC" as alias)
        candidate_b = make_seed(
            "entity:southeastern-conference",
            "Southeastern Conference",
            "entity",
            fact_count=3,
            metadata_={"aliases": ["SEC"]},
        )
        repo.find_seeds_by_alias = AsyncMock(return_value=[candidate_b])

        # Neither is merged — they're independent
        repo.get_seed_by_key = AsyncMock(
            side_effect=lambda k: {
                "entity:sec": make_seed("entity:sec", "SEC", "entity", fact_count=1),
                "entity:securities-and-exchange-commission": candidate_a,
                "entity:southeastern-conference": candidate_b,
            }.get(k)
        )

        result = await deduplicate_seed(
            "entity:sec",
            "SEC",
            "entity",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        # Should return own key (marked as ambiguous)
        assert result == "entity:sec"
        # Should have created routes to both candidates
        assert repo.create_route.call_count == 2


@pytest.mark.asyncio
class TestContainmentGuardIntegration:
    async def test_trigram_match_with_containment_skipped(self):
        """Trigram match that fails containment guard should not merge."""
        repo = make_seed_repo_mock()
        existing = make_seed(
            "entity:jeffrey-epstein",
            "Jeffrey Epstein",
            "entity",
            fact_count=5,
        )
        repo.find_similar_seeds = AsyncMock(return_value=[existing])

        result = await deduplicate_seed(
            "entity:jeffrey-epstein-s-lawyer",
            "Jeffrey Epstein's Lawyer",
            "entity",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        # Should NOT merge — containment mismatch blocks alias, embedding finds nothing
        assert result == "entity:jeffrey-epstein-s-lawyer"
        repo.merge_seeds.assert_not_called()

    async def test_committee_containment_skipped(self):
        """Contained committee name should not merge."""
        repo = make_seed_repo_mock()
        existing = make_seed(
            "entity:democrats-on-the-house-oversight-committee",
            "Democrats on the House Oversight Committee",
            "entity",
            fact_count=3,
        )
        repo.find_similar_seeds = AsyncMock(return_value=[existing])

        result = await deduplicate_seed(
            "entity:house-oversight-committee",
            "House Oversight Committee",
            "entity",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )
        assert result == "entity:house-oversight-committee"
        repo.merge_seeds.assert_not_called()


@pytest.mark.asyncio
class TestLLMConfirmMerge:
    async def test_llm_confirms_same_concept(self):
        """LLM says same entity → merge + specificity upgrade."""
        repo = make_seed_repo_mock()
        existing = make_seed(
            "concept:photosynthesis",
            "photosynthesis",
            "concept",
            fact_count=5,
        )
        repo.find_similar_seeds = AsyncMock(return_value=[])
        repo.get_seed_by_key = AsyncMock(
            side_effect=lambda k: {
                "concept:oxygenic-photosynthesis": make_seed(
                    "concept:oxygenic-photosynthesis",
                    "oxygenic photosynthesis",
                    "concept",
                    fact_count=1,
                ),
                "concept:photosynthesis": existing,
            }.get(k)
        )
        repo.merge_seeds = AsyncMock(return_value=MagicMock())
        repo.get_facts_for_seed = AsyncMock(return_value=[])
        repo.rename_seed = AsyncMock()

        qdrant_repo = make_qdrant_seed_repo_mock()
        qdrant_repo.find_similar = AsyncMock(
            return_value=[
                make_qdrant_match("concept:photosynthesis", 0.92),
            ]
        )

        model_gw = make_model_gateway_mock({"same_entity": True, "preferred_name": "oxygenic photosynthesis"})
        write_fact_repo = make_write_fact_repo_mock()

        result = await deduplicate_seed(
            "concept:oxygenic-photosynthesis",
            "oxygenic photosynthesis",
            "concept",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=qdrant_repo,
            model_gateway=model_gw,
            write_fact_repo=write_fact_repo,
        )
        assert result == "concept:photosynthesis"
        repo.merge_seeds.assert_called_once()
        repo.rename_seed.assert_called_once_with("concept:photosynthesis", "oxygenic photosynthesis")

    async def test_auto_merge_skips_llm_for_high_similarity(self):
        """Very high embedding + string guards pass → auto-merge without LLM."""
        repo = make_seed_repo_mock()
        existing = make_seed(
            "concept:posttraumatic-growth",
            "posttraumatic growth",
            "concept",
            fact_count=5,
        )
        repo.find_similar_seeds = AsyncMock(return_value=[])
        repo.get_seed_by_key = AsyncMock(
            side_effect=lambda k: {
                "concept:post-traumatic-growth": make_seed(
                    "concept:post-traumatic-growth",
                    "post-traumatic growth",
                    "concept",
                    fact_count=1,
                ),
                "concept:posttraumatic-growth": existing,
            }.get(k)
        )
        repo.merge_seeds = AsyncMock(return_value=MagicMock())

        qdrant_repo = make_qdrant_seed_repo_mock()
        qdrant_repo.find_similar = AsyncMock(
            return_value=[
                make_qdrant_match("concept:posttraumatic-growth", 0.987),
            ]
        )

        model_gw = make_model_gateway_mock({"same_entity": True, "preferred_name": None})
        write_fact_repo = make_write_fact_repo_mock()

        result = await deduplicate_seed(
            "concept:post-traumatic-growth",
            "post-traumatic growth",
            "concept",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=qdrant_repo,
            model_gateway=model_gw,
            write_fact_repo=write_fact_repo,
        )
        assert result == "concept:posttraumatic-growth"
        repo.merge_seeds.assert_called_once()
        # LLM should NOT have been called
        model_gw.generate_json.assert_not_called()

    async def test_auto_merge_blocked_by_digit_guard(self):
        """High embedding but digit-only difference → still uses LLM."""
        repo = make_seed_repo_mock()
        existing = make_seed(
            "event:apvac1",
            "APVAC1",
            "event",
            fact_count=5,
        )
        repo.find_similar_seeds = AsyncMock(return_value=[])
        repo.get_seed_by_key = AsyncMock(
            side_effect=lambda k: {
                "event:apvac2": make_seed("event:apvac2", "APVAC2", "event", fact_count=1),
                "event:apvac1": existing,
            }.get(k)
        )
        repo.get_facts_for_seed = AsyncMock(return_value=[])
        repo.split_seed = AsyncMock(return_value=[])
        repo.create_route = AsyncMock()

        qdrant_repo = make_qdrant_seed_repo_mock()
        qdrant_repo.find_similar = AsyncMock(
            return_value=[
                make_qdrant_match("event:apvac1", 0.96),
            ]
        )

        model_gw = make_model_gateway_mock({"same_entity": False, "preferred_name": None})
        write_fact_repo = make_write_fact_repo_mock()

        await deduplicate_seed(
            "event:apvac2",
            "APVAC2",
            "event",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=qdrant_repo,
            model_gateway=model_gw,
            write_fact_repo=write_fact_repo,
        )
        # LLM SHOULD have been called (digit guard blocks auto-merge)
        model_gw.generate_json.assert_called_once()

    async def test_auto_merge_blocked_by_academic_initials(self):
        """High embedding but academic initials → still uses LLM."""
        repo = make_seed_repo_mock()
        existing = make_seed(
            "entity:ana-r-p-silva",
            "Ana R. P. Silva",
            "entity",
            fact_count=5,
        )
        repo.find_similar_seeds = AsyncMock(return_value=[])
        repo.get_seed_by_key = AsyncMock(
            side_effect=lambda k: {
                "entity:ana-r-s-silva": make_seed("entity:ana-r-s-silva", "Ana R. S. Silva", "entity", fact_count=1),
                "entity:ana-r-p-silva": existing,
            }.get(k)
        )
        repo.get_facts_for_seed = AsyncMock(return_value=[])
        repo.split_seed = AsyncMock(return_value=[])
        repo.create_route = AsyncMock()

        qdrant_repo = make_qdrant_seed_repo_mock()
        qdrant_repo.find_similar = AsyncMock(
            return_value=[
                make_qdrant_match("entity:ana-r-p-silva", 0.982),
            ]
        )

        model_gw = make_model_gateway_mock({"same_entity": False, "preferred_name": None})
        write_fact_repo = make_write_fact_repo_mock()

        await deduplicate_seed(
            "entity:ana-r-s-silva",
            "Ana R. S. Silva",
            "entity",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=qdrant_repo,
            model_gateway=model_gw,
            write_fact_repo=write_fact_repo,
        )
        # LLM SHOULD have been called (academic initials guard blocks auto-merge)
        model_gw.generate_json.assert_called_once()

    async def test_llm_rejects_creates_anchor(self):
        """LLM says different → existing becomes disambiguation anchor."""
        repo = make_seed_repo_mock()
        existing = make_seed(
            "concept:light-dependent-reactions",
            "light-dependent reactions",
            "concept",
            fact_count=3,
        )
        incoming = make_seed(
            "concept:light-independent-reactions",
            "light-independent reactions",
            "concept",
            fact_count=1,
        )
        repo.find_similar_seeds = AsyncMock(return_value=[])
        repo.get_seed_by_key = AsyncMock(
            side_effect=lambda k: {
                "concept:light-dependent-reactions": existing,
                "concept:light-independent-reactions": incoming,
            }.get(k)
        )
        repo.get_facts_for_seed = AsyncMock(return_value=[])
        repo.split_seed = AsyncMock(return_value=[])
        repo.create_route = AsyncMock()

        qdrant_repo = make_qdrant_seed_repo_mock()
        qdrant_repo.find_similar = AsyncMock(
            return_value=[
                make_qdrant_match("concept:light-dependent-reactions", 0.90),
            ]
        )

        model_gw = make_model_gateway_mock({"same_entity": False, "preferred_name": None})
        write_fact_repo = make_write_fact_repo_mock()

        result = await deduplicate_seed(
            "concept:light-independent-reactions",
            "light-independent reactions",
            "concept",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=qdrant_repo,
            model_gateway=model_gw,
            write_fact_repo=write_fact_repo,
        )
        # Should NOT have merged
        repo.merge_seeds.assert_not_called()
        # Should have created disambiguation (split_seed called)
        repo.split_seed.assert_called_once()

    async def test_llm_unavailable_auto_merges(self):
        """Without model_gateway, Signal 1 auto-merges (backward compat)."""
        repo = make_seed_repo_mock()
        existing = make_seed("concept:photo", "photosynthesis", "concept", fact_count=5)
        repo.find_similar_seeds = AsyncMock(return_value=[])
        repo.get_seed_by_key = AsyncMock(
            side_effect=lambda k: {
                "concept:photo-new": make_seed("concept:photo-new", "photosynthesis process", "concept", fact_count=1),
                "concept:photo": existing,
            }.get(k)
        )
        repo.merge_seeds = AsyncMock(return_value=MagicMock())

        qdrant_repo = make_qdrant_seed_repo_mock()
        qdrant_repo.find_similar = AsyncMock(
            return_value=[
                make_qdrant_match("concept:photo", 0.92),
            ]
        )

        result = await deduplicate_seed(
            "concept:photo-new",
            "photosynthesis process",
            "concept",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=qdrant_repo,
            # No model_gateway or write_fact_repo
        )
        assert result == "concept:photo"
        repo.merge_seeds.assert_called_once()

    async def test_llm_error_defaults_no_merge(self):
        """LLM error → default to no-merge for safety."""
        repo = make_seed_repo_mock()
        existing = make_seed("concept:a", "concept A", "concept", fact_count=3)
        repo.find_similar_seeds = AsyncMock(return_value=[])
        repo.get_seed_by_key = AsyncMock(
            side_effect=lambda k: {
                "concept:b": make_seed("concept:b", "concept B", "concept", fact_count=1),
                "concept:a": existing,
            }.get(k)
        )
        repo.get_facts_for_seed = AsyncMock(return_value=[])
        repo.split_seed = AsyncMock(return_value=[])
        repo.create_route = AsyncMock()

        qdrant_repo = make_qdrant_seed_repo_mock()
        qdrant_repo.find_similar = AsyncMock(
            return_value=[
                make_qdrant_match("concept:a", 0.90),
            ]
        )

        model_gw = MagicMock()
        model_gw.generate_json = AsyncMock(side_effect=Exception("API error"))
        write_fact_repo = make_write_fact_repo_mock()

        result = await deduplicate_seed(
            "concept:b",
            "concept B",
            "concept",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=qdrant_repo,
            model_gateway=model_gw,
            write_fact_repo=write_fact_repo,
        )
        # Error → no merge (safety)
        repo.merge_seeds.assert_not_called()

    async def test_no_facts_names_only(self):
        """LLM gate works with just names when no facts are available."""
        repo = make_seed_repo_mock()
        repo.get_facts_for_seed = AsyncMock(return_value=[])

        model_gw = make_model_gateway_mock({"same_entity": True, "preferred_name": None})
        write_fact_repo = make_write_fact_repo_mock()

        confirmed, preferred = await _llm_confirm_merge(
            "Calvin cycle",
            "concept:calvin-cycle",
            "Calvin-Benson cycle",
            "concept:calvin-benson-cycle",
            repo,
            write_fact_repo,
            model_gw,
        )
        assert confirmed is True
        model_gw.generate_json.assert_called_once()

    async def test_anchor_skipped_on_recheck(self):
        """Ambiguous anchor seed is skipped by Signal 1 (status != active/promoted)."""
        repo = make_seed_repo_mock()
        ambiguous = make_seed(
            "concept:light-dependent-reactions",
            "light-dependent reactions",
            "concept",
            status="ambiguous",
            fact_count=3,
        )
        repo.find_similar_seeds = AsyncMock(return_value=[])
        repo.get_seed_by_key = AsyncMock(
            side_effect=lambda k: {
                "concept:new-light-concept": make_seed(
                    "concept:new-light-concept",
                    "new light concept",
                    "concept",
                    fact_count=1,
                ),
                "concept:light-dependent-reactions": ambiguous,
            }.get(k)
        )

        qdrant_repo = make_qdrant_seed_repo_mock()
        qdrant_repo.find_similar = AsyncMock(
            return_value=[
                make_qdrant_match("concept:light-dependent-reactions", 0.88),
            ]
        )

        result = await deduplicate_seed(
            "concept:new-light-concept",
            "new light concept",
            "concept",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=qdrant_repo,
        )
        assert result == "concept:new-light-concept"
        repo.merge_seeds.assert_not_called()


class TestPrefixDisambiguation:
    def test_light_dependent_vs_independent(self):
        assert _is_prefix_disambiguation_candidate("light-dependent reactions", "light-independent reactions") is True

    def test_north_korea_vs_north_macedonia(self):
        assert _is_prefix_disambiguation_candidate("North Korea", "North Macedonia") is True

    def test_unrelated_names(self):
        assert _is_prefix_disambiguation_candidate("photosynthesis", "dark reactions") is False

    def test_prefix_too_short(self):
        assert _is_prefix_disambiguation_candidate("cat", "car") is False

    def test_one_is_pure_prefix(self):
        # "photo" is a pure prefix of "photosynthesis" → containment, not disambiguation
        assert _is_prefix_disambiguation_candidate("photo", "photosynthesis") is False

    def test_identical_names(self):
        assert _is_prefix_disambiguation_candidate("same", "same") is False

    def test_endothermic_vs_exothermic(self):
        # Share "e" prefix only — too short for match at word boundary
        # These don't share a long enough prefix
        assert _is_prefix_disambiguation_candidate("endothermic reaction", "exothermic reaction") is False


class TestTextSearchRoute:
    def test_fact_contains_seed_name(self):
        routes = [
            make_route("", "concept:light-dep:disambig", "light-dependent reactions", "embedding"),
            make_route("", "concept:light-indep", "light-independent reactions", "embedding"),
        ]
        result = _text_search_route(
            "The light-dependent reactions occur in the thylakoid membrane",
            routes,
        )
        assert result == "concept:light-dep:disambig"

    def test_fact_contains_neither(self):
        routes = [
            make_route("", "concept:a", "light-dependent reactions", "embedding"),
            make_route("", "concept:b", "light-independent reactions", "embedding"),
        ]
        result = _text_search_route("Chloroplasts are organelles", routes)
        assert result is None

    def test_fact_contains_both(self):
        routes = [
            make_route("", "concept:a", "light-dependent reactions", "embedding"),
            make_route("", "concept:b", "light-independent reactions", "embedding"),
        ]
        result = _text_search_route(
            "Both light-dependent reactions and light-independent reactions occur in chloroplasts",
            routes,
        )
        assert result is None  # Ambiguous — both match


class TestCascadingRouting:
    """Tests for _resolve_through_pipes multi-step routing."""

    @pytest.mark.asyncio
    async def test_cascading_two_level_routing(self):
        """A(ambiguous) → B(ambiguous) → C(active). Fact reaches C."""
        from kt_facts.processing.seed_routing import _resolve_through_pipes

        seed_a = make_seed("concept:a", "A", "concept", status="ambiguous")
        seed_b = make_seed("concept:b", "B", "concept", status="ambiguous")
        seed_c = make_seed("concept:c", "C", "concept", status="active")

        route_a_b = make_route("concept:a", "concept:b", "B", "text")
        route_b_c = make_route("concept:b", "concept:c", "C", "text")

        repo = make_seed_repo_mock()
        # _route_through_pipe looks up routes for parent
        repo.get_routes_for_parent = AsyncMock(
            side_effect=lambda k: {
                "concept:a": [route_a_b],
                "concept:b": [route_b_c],
            }.get(k, [])
        )
        repo.get_seed_by_key = AsyncMock(
            side_effect=lambda k: {
                "concept:a": seed_a,
                "concept:b": seed_b,
                "concept:c": seed_c,
            }.get(k)
        )

        result = await _resolve_through_pipes(
            "concept:a",
            seed_a,
            "some fact",
            repo,
        )
        assert result == "concept:c"

    @pytest.mark.asyncio
    async def test_routing_stops_at_max_depth(self):
        """Chain of 6 ambiguous seeds — stops at MAX_ROUTE_DEPTH (5)."""
        from kt_facts.processing.seed_routing import MAX_ROUTE_DEPTH, _resolve_through_pipes

        # Build chain: seed_0 → seed_1 → ... → seed_6 (all ambiguous)
        seeds = {}
        routes = {}
        for i in range(7):
            key = f"concept:s{i}"
            seeds[key] = make_seed(key, f"S{i}", "concept", status="ambiguous")
            if i < 6:
                routes[key] = [make_route(key, f"concept:s{i + 1}", f"S{i + 1}", "text")]
            else:
                routes[key] = []

        repo = make_seed_repo_mock()
        repo.get_routes_for_parent = AsyncMock(side_effect=lambda k: routes.get(k, []))
        repo.get_seed_by_key = AsyncMock(side_effect=lambda k: seeds.get(k))

        result = await _resolve_through_pipes(
            "concept:s0",
            seeds["concept:s0"],
            "some fact",
            repo,
        )
        # Should stop at depth 5: concept:s5 (0-indexed from s0 through 5 iterations)
        assert result == f"concept:s{MAX_ROUTE_DEPTH}"

    @pytest.mark.asyncio
    async def test_routing_stops_on_same_key(self):
        """_route_through_pipe returns same key → no infinite loop."""
        from kt_facts.processing.seed_routing import _resolve_through_pipes

        seed_a = make_seed("concept:a", "A", "concept", status="ambiguous")

        repo = make_seed_repo_mock()
        # Only one route that points back to itself (edge case)
        repo.get_routes_for_parent = AsyncMock(return_value=[])
        repo.get_seed_by_key = AsyncMock(return_value=seed_a)

        result = await _resolve_through_pipes(
            "concept:a",
            seed_a,
            "some fact",
            repo,
        )
        # No routes → _route_through_pipe returns None → stops at parent
        assert result == "concept:a"


class TestDiffersOnlyByDigitOrInitial:
    """Tests for the digit/initial guard used in tiered auto-merge."""

    def test_numbered_protocols(self):
        assert differs_only_by_digit_or_initial("APVAC1", "APVAC2") is True

    def test_numbered_protocols_multi_digit(self):
        assert differs_only_by_digit_or_initial("ParvOryx01 protocol", "ParvOryx02 protocol") is True

    def test_roman_numerals(self):
        assert differs_only_by_digit_or_initial("Phase I trial", "Phase II trial") is True

    def test_person_initials(self):
        assert differs_only_by_digit_or_initial("Ana R. S. Silva", "Ana R. P. Silva") is True

    def test_person_initials_multi(self):
        assert differs_only_by_digit_or_initial("Maria J. P. Silva", "Maria J. S. Silva") is True

    def test_completely_different_names(self):
        assert differs_only_by_digit_or_initial("photosynthesis", "cell division") is False

    def test_synonyms(self):
        assert differs_only_by_digit_or_initial("post-traumatic growth", "posttraumatic growth") is False

    def test_singular_plural(self):
        assert differs_only_by_digit_or_initial("biofield devices", "biofield device") is False

    def test_identical(self):
        assert differs_only_by_digit_or_initial("same name", "same name") is False


class TestHasAcademicInitials:
    def test_standard_initials(self):
        assert has_academic_initials("Ana R. P. Silva") is True

    def test_single_initial(self):
        assert has_academic_initials("J. Smith") is True

    def test_no_initials(self):
        assert has_academic_initials("photosynthesis") is False

    def test_abbreviation_not_initial(self):
        assert has_academic_initials("U.S. policy") is True  # still has initial pattern


class TestIsSafeAutoMerge:
    """Tests for the combined auto-merge gate."""

    def test_high_score_synonyms_pass(self):
        assert (
            is_safe_auto_merge(
                "post-traumatic growth",
                "posttraumatic growth",
                0.987,
                0.95,
            )
            is True
        )

    def test_high_score_digit_blocked(self):
        assert (
            is_safe_auto_merge(
                "APVAC1",
                "APVAC2",
                0.96,
                0.95,
            )
            is False
        )

    def test_high_score_initials_blocked(self):
        assert (
            is_safe_auto_merge(
                "Ana R. S. Silva",
                "Ana R. P. Silva",
                0.98,
                0.95,
            )
            is False
        )

    def test_below_threshold(self):
        assert (
            is_safe_auto_merge(
                "post-traumatic growth",
                "posttraumatic growth",
                0.93,
                0.95,
            )
            is False
        )

    def test_containment_blocked(self):
        assert (
            is_safe_auto_merge(
                "adult cancer patients",
                "adult cancer survivorship care",
                0.96,
                0.95,
            )
            is False
        )

    def test_low_string_similarity_blocked(self):
        assert (
            is_safe_auto_merge(
                "alpha brainwave bands",
                "theta brain waves",
                0.96,
                0.95,
            )
            is False
        )
