"""Integration tests for trigram-based seed deduplication against real PostgreSQL.

These tests use real pg_trgm similarity to measure actual scores and validate
that find_similar_seeds() and deduplicate_seed() behave correctly with real data.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text

from kt_db.keys import make_seed_key
from kt_db.repositories.write_seeds import WriteSeedRepository


def _mock_embedding_service():
    svc = MagicMock()
    svc.embed_text = AsyncMock(return_value=[0.1] * 10)
    return svc


def _mock_qdrant_repo():
    repo = MagicMock()
    repo.upsert = AsyncMock()
    repo.find_similar = AsyncMock(return_value=[])
    return repo


# ── Helpers ──────────────────────────────────────────────────────────


async def _insert_seed(repo: WriteSeedRepository, name: str, node_type: str = "entity") -> str:
    """Insert a seed and return its key."""
    key = make_seed_key(node_type, name)
    await repo.upsert_seed(key, name, node_type)
    return key


async def _raw_similarity(session, a: str, b: str) -> float:
    """Query raw SELECT similarity(a, b) from PostgreSQL."""
    result = await session.execute(
        text("SELECT similarity(:a, :b)"), {"a": a, "b": b}
    )
    return float(result.scalar())


# ── Test classes ─────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestTrigramScores:
    """Query raw SELECT similarity() to see exact PostgreSQL trigram scores.

    These tests don't insert seeds — they just measure the similarity function
    output to inform threshold and guard decisions.
    """

    # Should merge (true positives — expect high similarity)
    @pytest.mark.parametrize(
        "name_a, name_b, label",
        [
            ("The Miami Herald", "Miami Herald", "article prefix"),
            ("Albert Einstein", "A. Einstein", "abbreviation"),
            ("Barack Obama", "President Barack Obama", "title prefix"),
            ("McDonald's", "McDonalds", "punctuation"),
            ("JP Morgan", "JPMorgan Chase", "spacing"),
            ("United States", "United States of America", "full name"),
        ],
    )
    async def test_true_positive_scores(self, write_db_session, name_a, name_b, label):
        score = await _raw_similarity(write_db_session, name_a, name_b)
        # Log the score for threshold tuning
        print(f"  TRUE_POS  {score:.4f}  {name_a!r} <-> {name_b!r}  ({label})")
        # True positives should have reasonable similarity
        assert score > 0.0, f"Expected nonzero similarity for {label}"

    # Should NOT merge (false positives — expect lower similarity)
    @pytest.mark.parametrize(
        "name_a, name_b, label",
        [
            (
                "U.S. Attorney for Southern District of New York",
                "U.S. Attorney's Office for Southern District of Florida",
                "different jurisdiction",
            ),
            ("2006 Arrest of Jeffrey Epstein", "July 6 2019 Arrest of Jeffrey Epstein", "different dates"),
            ("World War 1", "World War 2", "different number"),
            ("New York Times", "New York Post", "different publication"),
            ("Bank of America", "Bank of England", "different country"),
            ("University of California", "University of Michigan", "different university"),
            ("Supreme Court of the United States", "Supreme Court of Canada", "different court"),
            ("Jeffrey Epstein", "Jeffrey Epstein's Lawyer", "entity vs related entity"),
            (
                "House Oversight Committee",
                "Democrats on the House Oversight Committee",
                "subset entity",
            ),
        ],
    )
    async def test_false_positive_scores(self, write_db_session, name_a, name_b, label):
        score = await _raw_similarity(write_db_session, name_a, name_b)
        print(f"  FALSE_POS {score:.4f}  {name_a!r} <-> {name_b!r}  ({label})")
        # These exist to log scores — assertions come in threshold tests

    # Embedding-only (trigram won't catch — expect low similarity)
    @pytest.mark.parametrize(
        "name_a, name_b, label",
        [
            ("FBI", "Federal Bureau of Investigation", "acronym"),
            ("CIA", "Central Intelligence Agency", "acronym"),
            ("NYSE", "New York Stock Exchange", "acronym"),
        ],
    )
    async def test_embedding_only_scores(self, write_db_session, name_a, name_b, label):
        score = await _raw_similarity(write_db_session, name_a, name_b)
        print(f"  EMBED_ONLY {score:.4f}  {name_a!r} <-> {name_b!r}  ({label})")
        # These should be very low — trigram can't match acronyms to expansions
        assert score < 0.5, f"Unexpectedly high trigram score for acronym case {label}"


@pytest.mark.asyncio
class TestFindSimilarSeedsIntegration:
    """Insert real seeds, call find_similar_seeds(), check results at various thresholds."""

    async def test_article_prefix_found_at_default_threshold(self, write_db_session):
        repo = WriteSeedRepository(write_db_session)
        await _insert_seed(repo, "Miami Herald", "entity")
        await write_db_session.flush()

        results = await repo.find_similar_seeds("The Miami Herald", "entity", threshold=0.50)
        keys = [s.key for s in results]
        assert make_seed_key("entity", "Miami Herald") in keys

    async def test_abbreviation_found(self, write_db_session):
        repo = WriteSeedRepository(write_db_session)
        await _insert_seed(repo, "Albert Einstein", "entity")
        await write_db_session.flush()

        results = await repo.find_similar_seeds("A. Einstein", "entity", threshold=0.30)
        keys = [s.key for s in results]
        assert make_seed_key("entity", "Albert Einstein") in keys

    async def test_different_jurisdiction_not_found_at_high_threshold(self, write_db_session):
        repo = WriteSeedRepository(write_db_session)
        await _insert_seed(repo, "U.S. Attorney for Southern District of New York", "entity")
        await write_db_session.flush()

        results = await repo.find_similar_seeds(
            "U.S. Attorney's Office for Southern District of Florida",
            "entity",
            threshold=0.75,
        )
        keys = [s.key for s in results]
        assert make_seed_key("entity", "U.S. Attorney for Southern District of New York") not in keys

    async def test_different_wars_not_found(self, write_db_session):
        repo = WriteSeedRepository(write_db_session)
        await _insert_seed(repo, "World War 1", "event")
        await write_db_session.flush()

        # Even at a low threshold, "World War 1" vs "World War 2" should be
        # caught by our containment guard, not by threshold alone
        results = await repo.find_similar_seeds("World War 2", "event", threshold=0.75)
        keys = [s.key for s in results]
        assert make_seed_key("event", "World War 1") not in keys

    async def test_different_publications_not_found(self, write_db_session):
        repo = WriteSeedRepository(write_db_session)
        await _insert_seed(repo, "New York Times", "entity")
        await write_db_session.flush()

        results = await repo.find_similar_seeds("New York Post", "entity", threshold=0.75)
        keys = [s.key for s in results]
        assert make_seed_key("entity", "New York Times") not in keys

    async def test_type_filter_works(self, write_db_session):
        """Seeds of different node_type should not match."""
        repo = WriteSeedRepository(write_db_session)
        await _insert_seed(repo, "Quantum Mechanics", "concept")
        await write_db_session.flush()

        results = await repo.find_similar_seeds("Quantum Mechanics", "entity", threshold=0.30)
        keys = [s.key for s in results]
        assert make_seed_key("concept", "Quantum Mechanics") not in keys


@pytest.mark.asyncio
class TestDeduplicateSeedIntegration:
    """Full deduplicate_seed() with real DB — tests containment guard + threshold together."""

    async def test_article_prefix_merges(self, write_db_session):
        """'The Miami Herald' should merge with 'Miami Herald'."""
        from kt_facts.processing.seed_dedup import deduplicate_seed

        repo = WriteSeedRepository(write_db_session)
        existing_key = await _insert_seed(repo, "Miami Herald Newspaper", "entity")
        incoming_key = await _insert_seed(repo, "The Miami Herald Newspaper", "entity")
        await write_db_session.flush()

        result = await deduplicate_seed(
            incoming_key, "The Miami Herald Newspaper", "entity", repo,
            embedding_service=_mock_embedding_service(),
            qdrant_seed_repo=_mock_qdrant_repo(),
        )
        # Should merge (existing wins on tie as more canonical)
        assert result == existing_key

    async def test_punctuation_merges(self, write_db_session):
        """McDonald's should merge with McDonalds."""
        from kt_facts.processing.seed_dedup import deduplicate_seed

        repo = WriteSeedRepository(write_db_session)
        existing_key = await _insert_seed(repo, "McDonald's Corporation", "entity")
        incoming_key = await _insert_seed(repo, "McDonalds Corporation", "entity")
        await write_db_session.flush()

        result = await deduplicate_seed(
            incoming_key, "McDonalds Corporation", "entity", repo,
            embedding_service=_mock_embedding_service(),
            qdrant_seed_repo=_mock_qdrant_repo(),
        )
        assert result == existing_key

    async def test_different_jurisdiction_no_merge(self, write_db_session):
        """US Attorney offices in different states should NOT merge."""
        from kt_facts.processing.seed_dedup import deduplicate_seed

        repo = WriteSeedRepository(write_db_session)
        existing_key = await _insert_seed(repo, "U.S. Attorney for Southern District of New York", "entity")
        incoming_key = await _insert_seed(
            repo, "U.S. Attorney's Office for Southern District of Florida", "entity"
        )
        await write_db_session.flush()

        result = await deduplicate_seed(
            incoming_key,
            "U.S. Attorney's Office for Southern District of Florida",
            "entity",
            repo,
            embedding_service=_mock_embedding_service(),
            qdrant_seed_repo=_mock_qdrant_repo(),
        )
        assert result == incoming_key  # Should NOT merge

    async def test_different_arrest_dates_no_merge(self, write_db_session):
        """Different arrest dates for same person should NOT merge."""
        from kt_facts.processing.seed_dedup import deduplicate_seed

        repo = WriteSeedRepository(write_db_session)
        existing_key = await _insert_seed(repo, "2006 Arrest of Jeffrey Epstein", "event")
        incoming_key = await _insert_seed(repo, "July 6 2019 Arrest of Jeffrey Epstein", "event")
        await write_db_session.flush()

        result = await deduplicate_seed(
            incoming_key,
            "July 6 2019 Arrest of Jeffrey Epstein",
            "event",
            repo,
            embedding_service=_mock_embedding_service(),
            qdrant_seed_repo=_mock_qdrant_repo(),
        )
        assert result == incoming_key  # Should NOT merge

    async def test_epstein_vs_lawyer_no_merge(self, write_db_session):
        """'Jeffrey Epstein' should NOT merge with 'Jeffrey Epstein's Lawyer'."""
        from kt_facts.processing.seed_dedup import deduplicate_seed

        repo = WriteSeedRepository(write_db_session)
        existing_key = await _insert_seed(repo, "Jeffrey Epstein", "entity")
        incoming_key = await _insert_seed(repo, "Jeffrey Epstein's Lawyer", "entity")
        await write_db_session.flush()

        result = await deduplicate_seed(
            incoming_key, "Jeffrey Epstein's Lawyer", "entity", repo,
            embedding_service=_mock_embedding_service(),
            qdrant_seed_repo=_mock_qdrant_repo(),
        )
        assert result == incoming_key  # Should NOT merge

    async def test_different_banks_no_merge(self, write_db_session):
        """'Bank of America' should NOT merge with 'Bank of England'."""
        from kt_facts.processing.seed_dedup import deduplicate_seed

        repo = WriteSeedRepository(write_db_session)
        existing_key = await _insert_seed(repo, "Bank of America", "entity")
        incoming_key = await _insert_seed(repo, "Bank of England", "entity")
        await write_db_session.flush()

        result = await deduplicate_seed(
            incoming_key, "Bank of England", "entity", repo,
            embedding_service=_mock_embedding_service(),
            qdrant_seed_repo=_mock_qdrant_repo(),
        )
        assert result == incoming_key  # Should NOT merge

    async def test_different_universities_no_merge(self, write_db_session):
        """'University of California' should NOT merge with 'University of Michigan'."""
        from kt_facts.processing.seed_dedup import deduplicate_seed

        repo = WriteSeedRepository(write_db_session)
        existing_key = await _insert_seed(repo, "University of California", "entity")
        incoming_key = await _insert_seed(repo, "University of Michigan", "entity")
        await write_db_session.flush()

        result = await deduplicate_seed(
            incoming_key, "University of Michigan", "entity", repo,
            embedding_service=_mock_embedding_service(),
            qdrant_seed_repo=_mock_qdrant_repo(),
        )
        assert result == incoming_key  # Should NOT merge
