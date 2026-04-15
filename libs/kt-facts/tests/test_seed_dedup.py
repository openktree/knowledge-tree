"""Tests for seed deduplication — pending-first pipeline.

Pipeline under test:
  1. DB text search (find_seeds_by_keys_or_aliases)
  2. Qdrant embedding search (find_similar)
  3. LLM multiplex → merge_into_seed | new_disambig_path
  4. Genesis path (no candidates) → promote pending + suggest_disambig
"""

from __future__ import annotations

import uuid
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
)

from kt_facts.processing.seed_dedup import (
    _merge_pair,
    _promote_and_genesis_disambig,
    deduplicate_seed,
    embed_and_upsert_seed,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _seed_map(*seeds):
    """Build {key: seed} side_effect for get_seed_by_key."""
    m = {s.key: s for s in seeds}
    async def _get(k):
        return m.get(k)
    return _get


# ── deduplicate_seed ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestDeduplicateSeed:
    async def test_no_candidates_returns_same_key(self):
        """No text or embedding candidates → genesis path, same key returned."""
        repo = make_seed_repo_mock()
        pending = make_seed("albert-einstein", "Albert Einstein", "entity", status="pending")
        repo.get_seed_by_key = AsyncMock(return_value=pending)

        result = await deduplicate_seed(
            "albert-einstein",
            "Albert Einstein",
            "entity",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
        )

        assert result == "albert-einstein"
        repo.set_status.assert_called_once_with("albert-einstein", "active")

    async def test_text_candidate_merge_via_llm(self):
        """Text search finds candidate → LLM says merge → merge_seeds called."""
        existing = make_seed("albert-einstein", "Albert Einstein", "entity", fact_count=10)
        incoming = make_seed("a-einstein", "A. Einstein", "entity", status="pending", fact_count=1)

        repo = make_seed_repo_mock()
        repo.find_seeds_by_keys_or_aliases = AsyncMock(return_value=[existing])
        repo.get_seed_by_key = AsyncMock(side_effect=_seed_map(existing, incoming))

        gw = make_model_gateway_mock({
            "action": "merge_into_seed",
            "target_seed_key": "albert-einstein",
            "reason": "same person",
        })

        result = await deduplicate_seed(
            "a-einstein",
            "A. Einstein",
            "entity",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
            model_gateway=gw,
        )

        assert result == "albert-einstein"
        repo.merge_seeds.assert_called_once()

    async def test_embedding_candidate_merge_via_llm(self):
        """Embedding search finds candidate (text search empty) → LLM merges."""
        existing = make_seed("homeopathy", "Homeopathy", "concept", fact_count=5)
        incoming_stub = make_seed("homeopathic-medicine", "Homeopathic Medicine", "concept", status="pending")

        repo = make_seed_repo_mock()
        repo.find_seeds_by_keys_or_aliases = AsyncMock(return_value=[])
        repo.get_seed_by_key = AsyncMock(side_effect=_seed_map(existing, incoming_stub))

        qdrant = make_qdrant_seed_repo_mock(
            hits=[make_qdrant_match("homeopathy", 0.92)]
        )
        gw = make_model_gateway_mock({
            "action": "merge_into_seed",
            "target_seed_key": "homeopathy",
            "reason": "synonym",
        })

        result = await deduplicate_seed(
            "homeopathic-medicine",
            "Homeopathic Medicine",
            "concept",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=qdrant,
            model_gateway=gw,
        )

        assert result == "homeopathy"

    async def test_llm_new_disambig_path_action(self):
        """LLM says new_disambig_path → _apply_disambig_path called, key unchanged."""
        existing = make_seed("mercury", "Mercury", "concept", fact_count=8)
        incoming = make_seed("mercury-2", "Mercury", "concept", status="pending", fact_count=1)

        repo = make_seed_repo_mock()
        repo.find_seeds_by_keys_or_aliases = AsyncMock(return_value=[existing])
        repo.get_seed_by_key = AsyncMock(side_effect=_seed_map(existing, incoming))
        repo.get_facts_for_seed = AsyncMock(return_value=[uuid.uuid4()])
        repo.get_routes_for_parent = AsyncMock(return_value=[])

        gw = make_model_gateway_mock({
            "action": "new_disambig_path",
            "target_seed_key": "mercury",
            "incoming_disambig_label": "Mercury (planet)",
            "existing_disambig_label": "Mercury (element)",
            "reason": "different referents",
        })

        result = await deduplicate_seed(
            "mercury-2",
            "Mercury",
            "concept",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
            model_gateway=gw,
        )

        assert result == "mercury-2"
        repo.split_seed.assert_called()

    async def test_no_llm_with_candidates_merges_into_best(self):
        """No model_gateway + candidates → default merge into highest fact_count."""
        existing = make_seed("albert-einstein", "Albert Einstein", "entity", fact_count=10)
        incoming = make_seed("a-einstein", "A. Einstein", "entity", status="pending")

        repo = make_seed_repo_mock()
        repo.find_seeds_by_keys_or_aliases = AsyncMock(return_value=[existing])
        repo.get_seed_by_key = AsyncMock(side_effect=_seed_map(existing, incoming))

        result = await deduplicate_seed(
            "a-einstein",
            "A. Einstein",
            "entity",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
            model_gateway=None,
        )

        assert result == "albert-einstein"
        repo.merge_seeds.assert_called_once()

    async def test_llm_invalid_target_falls_back_to_best_candidate(self):
        """LLM returns unknown target_seed_key → fall back to highest fact_count candidate."""
        existing = make_seed("albert-einstein", "Albert Einstein", "entity", fact_count=10)
        incoming = make_seed("a-einstein", "A. Einstein", "entity", status="pending")

        repo = make_seed_repo_mock()
        repo.find_seeds_by_keys_or_aliases = AsyncMock(return_value=[existing])
        repo.get_seed_by_key = AsyncMock(side_effect=_seed_map(existing, incoming))

        gw = make_model_gateway_mock({
            "action": "merge_into_seed",
            "target_seed_key": "nonexistent-key",
            "reason": "test",
        })

        result = await deduplicate_seed(
            "a-einstein",
            "A. Einstein",
            "entity",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
            model_gateway=gw,
        )

        # Fell back to best candidate
        assert result == "albert-einstein"

    async def test_text_search_error_falls_through_to_embedding(self):
        """Text search failure → pipeline continues with embedding candidates."""
        existing = make_seed("albert-einstein", "Albert Einstein", "entity", fact_count=5)
        incoming = make_seed("a-einstein", "A. Einstein", "entity", status="pending")

        repo = make_seed_repo_mock()
        repo.find_seeds_by_keys_or_aliases = AsyncMock(side_effect=Exception("DB error"))
        repo.get_seed_by_key = AsyncMock(side_effect=_seed_map(existing, incoming))

        qdrant = make_qdrant_seed_repo_mock(hits=[make_qdrant_match("albert-einstein", 0.94)])
        gw = make_model_gateway_mock({
            "action": "merge_into_seed",
            "target_seed_key": "albert-einstein",
            "reason": "same",
        })

        result = await deduplicate_seed(
            "a-einstein",
            "A. Einstein",
            "entity",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=qdrant,
            model_gateway=gw,
        )

        assert result == "albert-einstein"

    async def test_merged_qdrant_hit_excluded(self):
        """Qdrant hit with status=merged is excluded from candidates."""
        merged = make_seed("old-key", "Old Name", "entity", status="merged")
        incoming = make_seed("new-key", "New Name", "entity", status="pending")

        repo = make_seed_repo_mock()
        repo.find_seeds_by_keys_or_aliases = AsyncMock(return_value=[])
        repo.get_seed_by_key = AsyncMock(side_effect=_seed_map(merged, incoming))

        qdrant = make_qdrant_seed_repo_mock(hits=[make_qdrant_match("old-key", 0.95)])

        result = await deduplicate_seed(
            "new-key",
            "New Name",
            "entity",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=qdrant,
        )

        # merged seed filtered → no candidates → genesis
        assert result == "new-key"
        repo.set_status.assert_called_with("new-key", "active")

    async def test_practitioner_vs_practice_no_merge(self):
        """Homeopath ≠ Homeopathy — LLM must fire and return disambig, not merge."""
        homeopathy = make_seed("homeopathy", "Homeopathy", "concept", fact_count=20)
        incoming = make_seed("homeopath", "Homeopath", "concept", status="pending")

        repo = make_seed_repo_mock()
        repo.find_seeds_by_keys_or_aliases = AsyncMock(return_value=[homeopathy])
        repo.get_seed_by_key = AsyncMock(side_effect=_seed_map(homeopathy, incoming))
        repo.get_facts_for_seed = AsyncMock(return_value=[])
        repo.get_routes_for_parent = AsyncMock(return_value=[])

        gw = make_model_gateway_mock({
            "action": "new_disambig_path",
            "target_seed_key": "homeopathy",
            "incoming_disambig_label": "Homeopath (practitioner)",
            "existing_disambig_label": "Homeopathy (practice)",
            "reason": "practitioner vs practice",
        })

        result = await deduplicate_seed(
            "homeopath",
            "Homeopath",
            "concept",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
            model_gateway=gw,
        )

        # merge_seeds is called internally to fold incoming into its labelled child,
        # but NOT to merge homeopath into homeopathy (different referents)
        merge_calls = [str(c) for c in repo.merge_seeds.call_args_list]
        assert not any("homeopathy" in c and "homeopath" in c and "multiplex_fold" not in c for c in merge_calls)
        assert result == "homeopath"

    async def test_aliases_passed_to_text_search(self):
        """aliases kwarg is forwarded to find_seeds_by_keys_or_aliases as extra keys."""
        repo = make_seed_repo_mock()
        pending = make_seed("nate-silver", "Nate Silver", "entity", status="pending")
        repo.get_seed_by_key = AsyncMock(return_value=pending)

        await deduplicate_seed(
            "nate-silver",
            "Nate Silver",
            "entity",
            repo,
            embedding_service=make_embedding_service_mock(),
            qdrant_seed_repo=make_qdrant_seed_repo_mock(),
            aliases=["nathaniel-silver"],
        )

        call_args = repo.find_seeds_by_keys_or_aliases.call_args
        assert "nathaniel-silver" in call_args.kwargs.get("keys", call_args.args[0] if call_args.args else [])


# ── _merge_pair ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestMergePair:
    async def test_higher_fact_count_wins(self):
        """Seed with more facts wins."""
        winner = make_seed("alpha", "Alpha", "concept", fact_count=10)
        loser = make_seed("beta", "Beta", "concept", fact_count=2)

        repo = make_seed_repo_mock()
        repo.get_seed_by_key = AsyncMock(side_effect=_seed_map(winner, loser))

        result = await _merge_pair("beta", "alpha", repo, reason="test")
        assert result == "alpha"
        repo.merge_seeds.assert_called_once()

    async def test_incoming_wins_when_more_facts(self):
        """Incoming beats existing when incoming has more facts."""
        incoming = make_seed("alpha", "Alpha", "concept", fact_count=20)
        existing = make_seed("beta", "Beta", "concept", fact_count=2)

        repo = make_seed_repo_mock()
        repo.get_seed_by_key = AsyncMock(side_effect=_seed_map(incoming, existing))

        result = await _merge_pair("alpha", "beta", repo, reason="test")
        assert result == "alpha"

    async def test_longest_name_wins_canonical(self):
        """Winner adopts loser's name when loser name is longer."""
        winner = make_seed("abc", "ABC", "concept", fact_count=10)
        loser = make_seed("american-broadcasting-company", "American Broadcasting Company", "concept", fact_count=2)

        repo = make_seed_repo_mock()
        repo.get_seed_by_key = AsyncMock(side_effect=_seed_map(winner, loser))

        await _merge_pair(
            "american-broadcasting-company", "abc", repo, reason="test"
        )

        repo.rename_seed.assert_called_once_with("abc", "American Broadcasting Company")

    async def test_loser_aliases_propagate_to_winner(self):
        """Loser key + loser aliases all added to winner aliases."""
        winner = make_seed("alpha", "Alpha", "concept", fact_count=10, aliases=["al"])
        loser = make_seed("beta", "Beta", "concept", fact_count=2, aliases=["b"])

        repo = make_seed_repo_mock()
        repo.get_seed_by_key = AsyncMock(side_effect=_seed_map(winner, loser))

        await _merge_pair("beta", "alpha", repo, reason="test")

        call_args = repo.merge_aliases_into_winner.call_args
        extra = call_args.args[1] if call_args.args else call_args.kwargs.get("extra_aliases", [])
        assert "beta" in extra
        assert "b" in extra

    async def test_missing_seed_returns_incoming(self):
        """If either seed not found, no merge, return incoming."""
        repo = make_seed_repo_mock()
        repo.get_seed_by_key = AsyncMock(return_value=None)

        result = await _merge_pair("a", "b", repo, reason="test")
        assert result == "a"
        repo.merge_seeds.assert_not_called()


# ── _promote_and_genesis_disambig ────────────────────────────────────────────

@pytest.mark.asyncio
class TestPromoteAndGenesis:
    async def test_skips_active_seed(self):
        """Already active seeds skip the genesis path entirely."""
        repo = make_seed_repo_mock()
        repo.get_seed_by_key = AsyncMock(
            return_value=make_seed("mars", "Mars", "concept", status="active")
        )

        await _promote_and_genesis_disambig("mars", "Mars", "concept", repo, model_gateway=None)

        repo.set_status.assert_not_called()

    async def test_skips_missing_seed(self):
        """Missing seed → no-op."""
        repo = make_seed_repo_mock()
        repo.get_seed_by_key = AsyncMock(return_value=None)

        await _promote_and_genesis_disambig("x", "X", "concept", repo, model_gateway=None)
        repo.set_status.assert_not_called()

    async def test_promotes_pending_to_active_no_llm(self):
        """Pending seed promoted to active when no model_gateway."""
        repo = make_seed_repo_mock()
        repo.get_seed_by_key = AsyncMock(
            return_value=make_seed("mars", "Mars", "concept", status="pending")
        )

        await _promote_and_genesis_disambig("mars", "Mars", "concept", repo, model_gateway=None)

        repo.set_status.assert_called_once_with("mars", "active")

    async def test_single_word_polysemous_name_creates_routes(self):
        """LLM returns 2+ paths → split_seed called to create child routes."""
        repo = make_seed_repo_mock()
        repo.get_seed_by_key = AsyncMock(
            return_value=make_seed("mercury", "Mercury", "concept", status="pending")
        )
        repo.get_facts_with_content_for_seed = AsyncMock(
            return_value=[(uuid.uuid4(), "Mercury is the closest planet to the Sun.")]
        )

        gw = MagicMock()
        gw.generate_json = AsyncMock(side_effect=[
            # suggest_disambig call
            {"paths": ["Mercury (planet)", "Mercury (element)", "Mercury (Roman god)"]},
            # route_facts_to_paths call
            {"assignments": []},
        ])

        with patch("kt_facts.processing.seed_dedup.get_settings") as mock_settings:
            s = MagicMock()
            s.seed_suggest_disambig_enabled = True
            s.seed_dedup_llm_model = ""
            s.decomposition_model = "test-model"
            mock_settings.return_value = s

            await _promote_and_genesis_disambig("mercury", "Mercury", "concept", repo, model_gateway=gw)

        repo.split_seed.assert_called_once()
        repo.set_status.assert_any_call("mercury", "ambiguous")

    async def test_no_paths_returns_active_only(self):
        """LLM returns empty paths → seed stays active, no routes."""
        repo = make_seed_repo_mock()
        repo.get_seed_by_key = AsyncMock(
            return_value=make_seed("uniquename", "Uniquename", "concept", status="pending")
        )

        gw = MagicMock()
        gw.generate_json = AsyncMock(return_value={"paths": []})

        with patch("kt_facts.processing.seed_dedup.get_settings") as mock_settings:
            s = MagicMock()
            s.seed_suggest_disambig_enabled = True
            s.seed_dedup_llm_model = ""
            s.decomposition_model = "test-model"
            mock_settings.return_value = s

            await _promote_and_genesis_disambig("uniquename", "Uniquename", "concept", repo, model_gateway=gw)

        repo.split_seed.assert_not_called()
        repo.set_status.assert_called_with("uniquename", "active")

    async def test_suggest_disambig_disabled_by_setting(self):
        """seed_suggest_disambig_enabled=False → active only, no LLM call."""
        repo = make_seed_repo_mock()
        repo.get_seed_by_key = AsyncMock(
            return_value=make_seed("mars", "Mars", "concept", status="pending")
        )
        gw = MagicMock()

        with patch("kt_facts.processing.seed_dedup.get_settings") as mock_settings:
            s = MagicMock()
            s.seed_suggest_disambig_enabled = False
            mock_settings.return_value = s

            await _promote_and_genesis_disambig("mars", "Mars", "concept", repo, model_gateway=gw)

        gw.generate_json.assert_not_called()
        repo.set_status.assert_called_with("mars", "active")


# ── embed_and_upsert_seed ─────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestEmbedAndUpsertSeed:
    async def test_embeds_and_upserts(self):
        emb = make_embedding_service_mock([0.5] * 10)
        qdrant = make_qdrant_seed_repo_mock()

        await embed_and_upsert_seed(
            seed_key="mars",
            name="Mars",
            node_type="concept",
            embedding_service=emb,
            qdrant_seed_repo=qdrant,
        )

        emb.embed_text.assert_called_once_with("Mars")
        qdrant.upsert.assert_called_once()

    async def test_embed_failure_logs_warning(self):
        """Embed failure should not raise — just log warning."""
        emb = make_embedding_service_mock()
        emb.embed_text = AsyncMock(side_effect=Exception("API error"))
        qdrant = make_qdrant_seed_repo_mock()

        # Should not raise
        await embed_and_upsert_seed("mars", "Mars", "concept", emb, qdrant)
        qdrant.upsert.assert_not_called()
