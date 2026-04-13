"""Tests for seed routing — disambiguation pipes and phonetic matching."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from seed_fixtures import make_route, make_seed, make_seed_repo_mock

from kt_facts.processing.seed_heuristics import (
    build_seed_context,
    compute_context_hash,
    compute_phonetic_code,
)
from kt_facts.processing.seed_routing import (
    maybe_re_embed_seed,
    route_seed,
)


@pytest.mark.asyncio
class TestRouteActiveAndPromoted:
    async def test_active_seed_returns_same_key(self):
        repo = make_seed_repo_mock()
        repo.get_seed_by_key = AsyncMock(return_value=make_seed("mars", "Mars", "entity", status="active"))
        result = await route_seed("Mars", "Mars is red", repo)
        assert result == "mars"

    async def test_promoted_seed_returns_same_key(self):
        repo = make_seed_repo_mock()
        repo.get_seed_by_key = AsyncMock(return_value=make_seed("mars", "Mars", "entity", status="promoted"))
        result = await route_seed("Mars", "Mars is red", repo)
        assert result == "mars"


@pytest.mark.asyncio
class TestRouteMergedSeed:
    async def test_follows_merge_chain(self):
        repo = make_seed_repo_mock()
        merged = make_seed("mars", "Mars", "entity", status="merged", merged_into_key="mars-planet")
        winner = make_seed("mars-planet", "Mars (planet)", "entity", status="active")

        repo.get_seed_by_key = AsyncMock(
            side_effect=lambda k: {
                "mars": merged,
                "mars-planet": winner,
            }.get(k)
        )

        result = await route_seed("Mars", "Mars is red", repo)
        assert result == "mars-planet"

    async def test_merge_chain_to_ambiguous_routes_through_pipe(self):
        repo = make_seed_repo_mock()
        merged = make_seed("m", "M", "entity", status="merged", merged_into_key="mars")
        ambiguous = make_seed("mars", "Mars", "entity", status="ambiguous")

        routes = [
            make_route("mars", "mars-planet", "planet"),
            make_route("mars", "mars-god", "Roman god"),
        ]
        repo.get_seed_by_key = AsyncMock(
            side_effect=lambda k: {
                "m": merged,
                "mars": ambiguous,
            }.get(k)
        )
        repo.get_routes_for_parent = AsyncMock(return_value=routes)

        # Without embedding service, returns first child
        result = await route_seed("M", "Mars is a planet", repo)
        assert result == "mars-planet"


@pytest.mark.asyncio
class TestRouteAmbiguousSeed:
    async def test_routes_to_best_embedding_match(self):
        repo = make_seed_repo_mock()
        ambiguous = make_seed("mars", "Mars", "entity", status="ambiguous")
        repo.get_seed_by_key = AsyncMock(return_value=ambiguous)

        routes = [
            make_route("mars", "mars-planet", "planet"),
            make_route("mars", "mars-god", "Roman god"),
        ]
        repo.get_routes_for_parent = AsyncMock(return_value=routes)

        emb_svc = MagicMock()
        emb_svc.embed_text = AsyncMock(return_value=[0.1] * 10)

        # Qdrant returns planet as best match
        planet_match = MagicMock()
        planet_match.seed_key = "mars-planet"
        planet_match.score = 0.92
        god_match = MagicMock()
        god_match.seed_key = "mars-god"
        god_match.score = 0.65

        qdrant_repo = MagicMock()
        qdrant_repo.find_similar = AsyncMock(return_value=[planet_match, god_match])

        result = await route_seed(
            "Mars",
            "Mars has a thin atmosphere",
            repo,
            embedding_service=emb_svc,
            qdrant_seed_repo=qdrant_repo,
        )
        assert result == "mars-planet"

    async def test_llm_fallback_when_scores_close(self):
        repo = make_seed_repo_mock()
        ambiguous = make_seed("mars", "Mars", "entity", status="ambiguous")
        repo.get_seed_by_key = AsyncMock(return_value=ambiguous)

        routes = [
            make_route("mars", "mars-planet", "planet"),
            make_route("mars", "mars-god", "Roman god"),
        ]
        repo.get_routes_for_parent = AsyncMock(return_value=routes)

        emb_svc = MagicMock()
        emb_svc.embed_text = AsyncMock(return_value=[0.1] * 10)

        # Very close scores trigger LLM
        planet_match = MagicMock()
        planet_match.seed_key = "mars-planet"
        planet_match.score = 0.86
        god_match = MagicMock()
        god_match.seed_key = "mars-god"
        god_match.score = 0.84

        qdrant_repo = MagicMock()
        qdrant_repo.find_similar = AsyncMock(return_value=[planet_match, god_match])

        gateway = MagicMock()
        gateway.default_model = "test-model"
        gateway.generate_json = AsyncMock(return_value={"choice": 2})

        result = await route_seed(
            "Mars",
            "Mars was worshipped in Rome",
            repo,
            embedding_service=emb_svc,
            qdrant_seed_repo=qdrant_repo,
            model_gateway=gateway,
        )
        assert result == "mars-god"

    async def test_single_route_returns_only_child(self):
        repo = make_seed_repo_mock()
        ambiguous = make_seed("mars", "Mars", "entity", status="ambiguous")
        repo.get_seed_by_key = AsyncMock(return_value=ambiguous)

        routes = [make_route("mars", "mars-planet", "planet")]
        repo.get_routes_for_parent = AsyncMock(return_value=routes)

        result = await route_seed("Mars", "Red planet", repo)
        assert result == "mars-planet"


@pytest.mark.asyncio
class TestPhoneticRouting:
    async def test_phonetic_match_to_active_seed(self):
        repo = make_seed_repo_mock()
        repo.get_seed_by_key = AsyncMock(return_value=None)  # Not found by key

        existing = make_seed("photosynthesis", "photosynthesis", "concept", fact_count=10)
        repo.find_by_phonetic = AsyncMock(return_value=[existing])
        repo.find_similar_seeds = AsyncMock(return_value=[existing])

        result = await route_seed("photosyntesis", "Plants use photosynthesis", repo)
        assert result == "photosynthesis"

    async def test_phonetic_without_trigram_confirmation_no_match(self):
        repo = make_seed_repo_mock()
        repo.get_seed_by_key = AsyncMock(return_value=None)

        existing = make_seed("photosynthesis", "photosynthesis", "concept")
        repo.find_by_phonetic = AsyncMock(return_value=[existing])
        # Trigram doesn't confirm
        repo.find_similar_seeds = AsyncMock(return_value=[])

        result = await route_seed("totally-different", "some fact", repo)
        assert result == "totally-different"

    async def test_phonetic_match_to_ambiguous_routes_through_pipe(self):
        repo = make_seed_repo_mock()
        repo.get_seed_by_key = AsyncMock(return_value=None)

        ambiguous = make_seed("mars", "Mars", "entity", status="ambiguous")
        repo.find_by_phonetic = AsyncMock(return_value=[ambiguous])
        repo.find_similar_seeds = AsyncMock(return_value=[ambiguous])

        routes = [make_route("mars", "mars-planet", "planet")]
        repo.get_routes_for_parent = AsyncMock(return_value=routes)

        result = await route_seed("Marz", "Red planet", repo)
        assert result == "mars-planet"


@pytest.mark.asyncio
class TestNotFoundSeed:
    async def test_new_seed_returns_original_key(self):
        repo = make_seed_repo_mock()
        result = await route_seed("NewConcept", "A new concept", repo)
        assert result == "newconcept"


class TestPhoneticCode:
    def test_basic_code(self):
        code = compute_phonetic_code("photosynthesis")
        assert code != ""

    def test_similar_spellings_same_code(self):
        # "Smith" and "Smyth" produce the same metaphone code
        code1 = compute_phonetic_code("Smith")
        code2 = compute_phonetic_code("Smyth")
        assert code1 == code2

    def test_empty_name(self):
        code = compute_phonetic_code("")
        # Should not raise
        assert isinstance(code, str)


class TestBuildSeedContext:
    def test_basic_context(self):
        ctx = build_seed_context("Mars", "entity")
        assert "Mars" in ctx
        assert "entity" in ctx

    def test_with_facts_and_aliases(self):
        ctx = build_seed_context(
            "Mars",
            "entity",
            top_facts=["Mars is a planet", "Mars has two moons"],
            aliases=["Red Planet"],
        )
        assert "Mars is a planet" in ctx
        assert "Red Planet" in ctx


class TestContextHash:
    def test_deterministic(self):
        h1 = compute_context_hash("test context")
        h2 = compute_context_hash("test context")
        assert h1 == h2

    def test_different_content_different_hash(self):
        h1 = compute_context_hash("context a")
        h2 = compute_context_hash("context b")
        assert h1 != h2


@pytest.mark.asyncio
class TestMaybeReEmbedSeed:
    async def test_no_reembed_below_threshold(self):
        repo = make_seed_repo_mock()
        emb_svc = MagicMock()
        qdrant = MagicMock()
        # fact_count=3 is not in default thresholds [5,15,50,100]
        await maybe_re_embed_seed(
            "test",
            3,
            repo,
            embedding_service=emb_svc,
            qdrant_seed_repo=qdrant,
        )
        emb_svc.embed_text.assert_not_called()

    async def test_reembed_at_threshold(self):
        repo = make_seed_repo_mock()
        seed = make_seed("test", "Test", "entity", fact_count=5)
        seed.context_hash = None
        repo.get_seed_by_key = AsyncMock(return_value=seed)
        repo.get_facts_for_seed = AsyncMock(return_value=[])

        emb_svc = MagicMock()
        emb_svc.embed_text = AsyncMock(return_value=[0.1] * 10)
        qdrant = MagicMock()
        qdrant.upsert = AsyncMock()

        await maybe_re_embed_seed(
            "test",
            5,
            repo,
            embedding_service=emb_svc,
            qdrant_seed_repo=qdrant,
        )
        emb_svc.embed_text.assert_called_once()
        qdrant.upsert.assert_called_once()
        repo.update_context_hash.assert_called_once()

    async def test_no_reembed_when_hash_unchanged(self):
        repo = make_seed_repo_mock()
        seed = make_seed("test", "Test", "entity", fact_count=5)
        # Pre-compute the hash for the expected context text
        expected_ctx = build_seed_context("Test", "entity")
        seed.context_hash = compute_context_hash(expected_ctx)
        repo.get_seed_by_key = AsyncMock(return_value=seed)
        repo.get_facts_for_seed = AsyncMock(return_value=[])

        emb_svc = MagicMock()
        qdrant = MagicMock()

        await maybe_re_embed_seed(
            "test",
            5,
            repo,
            embedding_service=emb_svc,
            qdrant_seed_repo=qdrant,
        )
        emb_svc.embed_text.assert_not_called()
