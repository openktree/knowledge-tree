"""Tests for WriteSeedRepository."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from kt_db.keys import key_to_uuid, make_node_key, make_seed_key
from kt_db.repositories.write_seeds import WriteSeedRepository


@pytest.mark.asyncio
class TestWriteSeedRepository:
    """Tests for seed CRUD operations."""

    async def test_upsert_seed_creates_new(self, write_db_session: AsyncSession) -> None:
        repo = WriteSeedRepository(write_db_session)
        key = make_seed_key("entity", "Albert Einstein")
        seed = await repo.upsert_seed(key, "Albert Einstein", "entity", "person")
        assert seed.key == key
        assert seed.name == "Albert Einstein"
        assert seed.node_type == "entity"
        assert seed.entity_subtype == "person"
        assert seed.status == "active"
        assert seed.fact_count == 0
        assert seed.seed_uuid == key_to_uuid(key)

    async def test_upsert_seed_does_not_change_fact_count(self, write_db_session: AsyncSession) -> None:
        repo = WriteSeedRepository(write_db_session)
        key = make_seed_key("concept", "quantum mechanics")
        await repo.upsert_seed(key, "quantum mechanics", "concept")
        seed = await repo.upsert_seed(key, "quantum mechanics", "concept")
        # fact_count stays 0 until refresh_fact_counts is called
        assert seed.fact_count == 0

    async def test_get_seed_by_key(self, write_db_session: AsyncSession) -> None:
        repo = WriteSeedRepository(write_db_session)
        key = make_seed_key("entity", "Marie Curie")
        await repo.upsert_seed(key, "Marie Curie", "entity", "person")
        seed = await repo.get_seed_by_key(key)
        assert seed is not None
        assert seed.name == "Marie Curie"

    async def test_get_seed_by_key_not_found(self, write_db_session: AsyncSession) -> None:
        repo = WriteSeedRepository(write_db_session)
        seed = await repo.get_seed_by_key("nonexistent:key")
        assert seed is None

    async def test_get_seeds_by_status(self, write_db_session: AsyncSession) -> None:
        repo = WriteSeedRepository(write_db_session)
        key = make_seed_key("concept", "relativity status test")
        await repo.upsert_seed(key, "relativity status test", "concept")
        seeds = await repo.get_seeds_by_status("active")
        assert any(s.key == key for s in seeds)

    async def test_link_fact(self, write_db_session: AsyncSession) -> None:
        repo = WriteSeedRepository(write_db_session)
        key = make_seed_key("entity", "Niels Bohr")
        await repo.upsert_seed(key, "Niels Bohr", "entity", "person")
        fact_id = uuid.uuid4()
        is_new = await repo.link_fact(key, fact_id, confidence=0.95)
        assert is_new is True
        # Linking same fact again should return False
        is_new2 = await repo.link_fact(key, fact_id)
        assert is_new2 is False

    async def test_get_facts_for_seed(self, write_db_session: AsyncSession) -> None:
        repo = WriteSeedRepository(write_db_session)
        key = make_seed_key("concept", "photosynthesis facts")
        await repo.upsert_seed(key, "photosynthesis facts", "concept")
        fid1, fid2 = uuid.uuid4(), uuid.uuid4()
        await repo.link_fact(key, fid1)
        await repo.link_fact(key, fid2)
        facts = await repo.get_facts_for_seed(key)
        assert set(facts) == {fid1, fid2}

    async def test_upsert_edge_candidate(self, write_db_session: AsyncSession) -> None:
        repo = WriteSeedRepository(write_db_session)
        key_a = make_seed_key("entity", "edge cand a")
        key_b = make_seed_key("entity", "edge cand b")
        a, b = sorted([key_a, key_b])
        await repo.upsert_seed(a, "edge cand a", "entity")
        await repo.upsert_seed(b, "edge cand b", "entity")
        fid = uuid.uuid4()
        await repo.upsert_edge_candidate(a, b, fid)
        candidates = await repo.get_candidates_for_seed(a)
        assert len(candidates) >= 1
        cand = [c for c in candidates if c.seed_key_a == a and c.seed_key_b == b][0]
        assert cand.fact_id == str(fid)
        assert cand.status == "pending"

    async def test_upsert_edge_candidate_multiple_facts(self, write_db_session: AsyncSession) -> None:
        """Each fact gets its own row."""
        repo = WriteSeedRepository(write_db_session)
        key_a = make_seed_key("concept", "edge multi a")
        key_b = make_seed_key("concept", "edge multi b")
        a, b = sorted([key_a, key_b])
        await repo.upsert_seed(a, "edge multi a", "concept")
        await repo.upsert_seed(b, "edge multi b", "concept")
        fid1, fid2 = uuid.uuid4(), uuid.uuid4()
        await repo.upsert_edge_candidate(a, b, fid1)
        await repo.upsert_edge_candidate(a, b, fid2)
        candidates = await repo.get_candidates_for_seed(a)
        pair_cands = [c for c in candidates if c.seed_key_a == a and c.seed_key_b == b]
        assert len(pair_cands) == 2
        fact_ids = {c.fact_id for c in pair_cands}
        assert str(fid1) in fact_ids
        assert str(fid2) in fact_ids

    async def test_upsert_edge_candidate_idempotent(self, write_db_session: AsyncSession) -> None:
        """Inserting the same fact twice is a no-op."""
        repo = WriteSeedRepository(write_db_session)
        key_a = make_seed_key("concept", "edge idem a")
        key_b = make_seed_key("concept", "edge idem b")
        a, b = sorted([key_a, key_b])
        await repo.upsert_seed(a, "edge idem a", "concept")
        await repo.upsert_seed(b, "edge idem b", "concept")
        fid = uuid.uuid4()
        await repo.upsert_edge_candidate(a, b, fid)
        await repo.upsert_edge_candidate(a, b, fid)  # duplicate
        candidates = await repo.get_candidates_for_seed(a)
        pair_cands = [c for c in candidates if c.seed_key_a == a and c.seed_key_b == b]
        assert len(pair_cands) == 1

    async def test_promote_seed(self, write_db_session: AsyncSession) -> None:
        repo = WriteSeedRepository(write_db_session)
        key = make_seed_key("entity", "Promoted Entity")
        await repo.upsert_seed(key, "Promoted Entity", "entity")
        node_key = make_seed_key("entity", "Promoted Entity")  # same key
        promoted = await repo.promote_seed(key, node_key)
        assert promoted is True
        seed = await repo.get_seed_by_key(key)
        assert seed is not None
        assert seed.status == "promoted"
        assert seed.promoted_node_key == node_key

    async def test_promote_seed_only_active(self, write_db_session: AsyncSession) -> None:
        repo = WriteSeedRepository(write_db_session)
        key = make_seed_key("entity", "Already Promoted")
        await repo.upsert_seed(key, "Already Promoted", "entity")
        await repo.promote_seed(key, key)
        # Second promote should fail (no longer active)
        promoted = await repo.promote_seed(key, key)
        assert promoted is False

    async def test_merge_seeds(self, write_db_session: AsyncSession) -> None:
        repo = WriteSeedRepository(write_db_session)
        winner_key = make_seed_key("entity", "Karl Marx Merge Winner")
        loser_key = make_seed_key("entity", "K Marx Merge Loser")
        await repo.upsert_seed(winner_key, "Karl Marx Merge Winner", "entity", "person")
        await repo.upsert_seed(loser_key, "K Marx Merge Loser", "entity", "person")
        fid1, fid2 = uuid.uuid4(), uuid.uuid4()
        await repo.link_fact(winner_key, fid1)
        await repo.link_fact(loser_key, fid2)

        merge = await repo.merge_seeds(loser_key, winner_key, reason="same person")
        assert merge.operation == "merge"
        assert merge.source_seed_key == loser_key
        assert merge.target_seed_key == winner_key

        # Loser should be merged
        loser = await repo.get_seed_by_key(loser_key)
        assert loser is not None
        assert loser.status == "merged"
        assert loser.merged_into_key == winner_key

        # Winner should have all facts
        winner_facts = await repo.get_facts_for_seed(winner_key)
        assert fid1 in winner_facts
        assert fid2 in winner_facts

    async def test_merge_seeds_with_overlapping_candidates(self, write_db_session: AsyncSession) -> None:
        """Merging seeds with overlapping edge candidates doesn't violate unique constraint."""
        repo = WriteSeedRepository(write_db_session)
        winner_key = make_seed_key("entity", "Merge Winner Overlap")
        loser_key = make_seed_key("entity", "Merge Loser Overlap")
        partner_key = make_seed_key("concept", "Merge Partner Overlap")
        await repo.upsert_seed(winner_key, "Merge Winner Overlap", "entity")
        await repo.upsert_seed(loser_key, "Merge Loser Overlap", "entity")
        await repo.upsert_seed(partner_key, "Merge Partner Overlap", "concept")

        # Both seeds have a candidate with the same partner + same fact
        shared_fid = uuid.uuid4()
        unique_fid = uuid.uuid4()
        wa, wb = sorted([winner_key, partner_key])
        la, lb = sorted([loser_key, partner_key])
        await repo.upsert_edge_candidate(wa, wb, shared_fid)
        await repo.upsert_edge_candidate(la, lb, shared_fid)
        await repo.upsert_edge_candidate(la, lb, unique_fid)

        # Merge should not raise
        await repo.merge_seeds(loser_key, winner_key, reason="overlap test")

        # Winner should have both facts (shared is deduped, unique is reassigned)
        wa2, wb2 = sorted([winner_key, partner_key])
        candidates = await repo.get_candidates_for_seed(wa2)
        pair_cands = [c for c in candidates if set([c.seed_key_a, c.seed_key_b]) == set([wa2, wb2])]
        fact_ids = {c.fact_id for c in pair_cands}
        assert str(shared_fid) in fact_ids
        assert str(unique_fid) in fact_ids

    async def test_get_edge_candidates_by_status(self, write_db_session: AsyncSession) -> None:
        repo = WriteSeedRepository(write_db_session)
        key_a = make_seed_key("concept", "status filter a")
        key_b = make_seed_key("concept", "status filter b")
        a, b = sorted([key_a, key_b])
        await repo.upsert_seed(a, "status filter a", "concept")
        await repo.upsert_seed(b, "status filter b", "concept")
        fid1, fid2 = uuid.uuid4(), uuid.uuid4()
        await repo.upsert_edge_candidate(a, b, fid1)
        await repo.upsert_edge_candidate(a, b, fid2)

        candidates = await repo.get_edge_candidates(status="pending", min_fact_count=2)
        assert any(c[0] == a and c[1] == b for c in candidates)

    async def test_reject_candidate_facts(self, write_db_session: AsyncSession) -> None:
        repo = WriteSeedRepository(write_db_session)
        key_a = make_seed_key("concept", "reject a")
        key_b = make_seed_key("concept", "reject b")
        a, b = sorted([key_a, key_b])
        await repo.upsert_seed(a, "reject a", "concept")
        await repo.upsert_seed(b, "reject b", "concept")
        fid1, fid2 = uuid.uuid4(), uuid.uuid4()
        await repo.upsert_edge_candidate(a, b, fid1)
        await repo.upsert_edge_candidate(a, b, fid2)

        # Reject one fact
        await repo.reject_candidate_facts(a, b, [str(fid1)])

        # Only fid2 should be pending
        pending = await repo.get_candidates_for_seed(a, status="pending")
        pair_pending = [c for c in pending if c.seed_key_a == a and c.seed_key_b == b]
        assert len(pair_pending) == 1
        assert pair_pending[0].fact_id == str(fid2)

        # fid1 should be rejected
        rejected = await repo.get_candidates_for_seed(a, status="rejected")
        pair_rejected = [c for c in rejected if c.seed_key_a == a and c.seed_key_b == b]
        assert len(pair_rejected) == 1
        assert pair_rejected[0].fact_id == str(fid1)

    async def test_accept_candidate_facts(self, write_db_session: AsyncSession) -> None:
        repo = WriteSeedRepository(write_db_session)
        key_a = make_seed_key("concept", "accept a")
        key_b = make_seed_key("concept", "accept b")
        a, b = sorted([key_a, key_b])
        await repo.upsert_seed(a, "accept a", "concept")
        await repo.upsert_seed(b, "accept b", "concept")
        fid1 = uuid.uuid4()
        await repo.upsert_edge_candidate(a, b, fid1)

        await repo.accept_candidate_facts(a, b, [str(fid1)])

        # Should not appear in pending
        pending = await repo.get_candidates_for_seed(a, status="pending")
        pair_pending = [c for c in pending if c.seed_key_a == a and c.seed_key_b == b]
        assert len(pair_pending) == 0

        # Should appear in accepted
        accepted = await repo.get_candidates_for_seed(a, status="accepted")
        pair_accepted = [c for c in accepted if c.seed_key_a == a and c.seed_key_b == b]
        assert len(pair_accepted) == 1

    async def test_rejected_facts_excluded_from_pending_query(self, write_db_session: AsyncSession) -> None:
        """New facts for same pair get status=pending even if others are rejected."""
        repo = WriteSeedRepository(write_db_session)
        key_a = make_seed_key("concept", "mix a")
        key_b = make_seed_key("concept", "mix b")
        a, b = sorted([key_a, key_b])
        await repo.upsert_seed(a, "mix a", "concept")
        await repo.upsert_seed(b, "mix b", "concept")
        fid1, fid2, fid3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        await repo.upsert_edge_candidate(a, b, fid1)
        await repo.upsert_edge_candidate(a, b, fid2)

        # Reject fid1
        await repo.reject_candidate_facts(a, b, [str(fid1)])

        # Add new fact — should be pending
        await repo.upsert_edge_candidate(a, b, fid3)

        pending = await repo.get_candidates_for_seed(a, status="pending")
        pair_pending = [c for c in pending if c.seed_key_a == a and c.seed_key_b == b]
        pending_fact_ids = {c.fact_id for c in pair_pending}
        assert str(fid2) in pending_fact_ids
        assert str(fid3) in pending_fact_ids
        assert str(fid1) not in pending_fact_ids


@pytest.mark.asyncio
class TestWriteSeedRoutes:
    """Tests for seed route (disambiguation pipe) operations."""

    async def test_create_route(self, write_db_session: AsyncSession) -> None:
        repo = WriteSeedRepository(write_db_session)
        parent_key = make_seed_key("entity", "Route Parent Test")
        child_key = make_seed_key("entity", "Route Child Test")
        await repo.upsert_seed(parent_key, "Route Parent Test", "entity")
        await repo.upsert_seed(child_key, "Route Child Test", "entity")

        route = await repo.create_route(parent_key, child_key, "child label")
        assert route.parent_seed_key == parent_key
        assert route.child_seed_key == child_key
        assert route.label == "child label"

    async def test_create_route_idempotent(self, write_db_session: AsyncSession) -> None:
        repo = WriteSeedRepository(write_db_session)
        parent_key = make_seed_key("entity", "Route Idem Parent")
        child_key = make_seed_key("entity", "Route Idem Child")
        await repo.upsert_seed(parent_key, "Route Idem Parent", "entity")
        await repo.upsert_seed(child_key, "Route Idem Child", "entity")

        await repo.create_route(parent_key, child_key, "label")
        await repo.create_route(parent_key, child_key, "label")  # no error
        routes = await repo.get_routes_for_parent(parent_key)
        assert len(routes) == 1

    async def test_get_routes_for_parent(self, write_db_session: AsyncSession) -> None:
        repo = WriteSeedRepository(write_db_session)
        parent_key = make_seed_key("entity", "Multi Route Parent")
        child1_key = make_seed_key("entity", "Multi Route Child 1")
        child2_key = make_seed_key("entity", "Multi Route Child 2")
        await repo.upsert_seed(parent_key, "Multi Route Parent", "entity")
        await repo.upsert_seed(child1_key, "Multi Route Child 1", "entity")
        await repo.upsert_seed(child2_key, "Multi Route Child 2", "entity")

        await repo.create_route(parent_key, child1_key, "child 1")
        await repo.create_route(parent_key, child2_key, "child 2")

        routes = await repo.get_routes_for_parent(parent_key)
        assert len(routes) == 2
        child_keys = {r.child_seed_key for r in routes}
        assert child1_key in child_keys
        assert child2_key in child_keys

    async def test_get_route_for_child(self, write_db_session: AsyncSession) -> None:
        repo = WriteSeedRepository(write_db_session)
        parent_key = make_seed_key("entity", "Reverse Lookup Parent")
        child_key = make_seed_key("entity", "Reverse Lookup Child")
        await repo.upsert_seed(parent_key, "Reverse Lookup Parent", "entity")
        await repo.upsert_seed(child_key, "Reverse Lookup Child", "entity")

        await repo.create_route(parent_key, child_key, "label")
        route = await repo.get_route_for_child(child_key)
        assert route is not None
        assert route.parent_seed_key == parent_key

    async def test_get_route_for_child_not_found(self, write_db_session: AsyncSession) -> None:
        repo = WriteSeedRepository(write_db_session)
        route = await repo.get_route_for_child("nonexistent:key")
        assert route is None

    async def test_split_seed_creates_routes(self, write_db_session: AsyncSession) -> None:
        repo = WriteSeedRepository(write_db_session)
        original_key = make_seed_key("entity", "Split Routes Test")
        await repo.upsert_seed(original_key, "Split Routes Test", "entity")
        fid1, fid2 = uuid.uuid4(), uuid.uuid4()
        await repo.link_fact(original_key, fid1)
        await repo.link_fact(original_key, fid2)

        new_seeds = [
            {
                "key": make_seed_key("entity", "Split Routes Test (planet)"),
                "name": "Split Routes Test (planet)",
                "node_type": "entity",
                "label": "planet",
            },
            {
                "key": make_seed_key("entity", "Split Routes Test (god)"),
                "name": "Split Routes Test (god)",
                "node_type": "entity",
                "label": "god",
            },
        ]
        fact_assignments = {
            new_seeds[0]["key"]: [fid1],
            new_seeds[1]["key"]: [fid2],
        }

        await repo.split_seed(original_key, new_seeds, fact_assignments, reason="test split")

        # Original should be ambiguous
        original = await repo.get_seed_by_key(original_key)
        assert original is not None
        assert original.status == "ambiguous"

        # Routes should exist
        routes = await repo.get_routes_for_parent(original_key)
        assert len(routes) == 2
        child_keys = {r.child_seed_key for r in routes}
        assert new_seeds[0]["key"] in child_keys
        assert new_seeds[1]["key"] in child_keys
        labels = {r.label for r in routes}
        assert "planet" in labels
        assert "god" in labels

    async def test_split_seed_preserves_extraction_role(self, write_db_session: AsyncSession) -> None:
        """split_seed() must preserve extraction_role on copied fact links."""
        repo = WriteSeedRepository(write_db_session)
        original_key = make_seed_key("entity", "Split Role Test")
        await repo.upsert_seed(original_key, "Split Role Test", "entity", "person")

        fid_mentioned = uuid.uuid4()
        fid_attribution = uuid.uuid4()
        fid_both = uuid.uuid4()
        await repo.link_fact(original_key, fid_mentioned, extraction_role="mentioned")
        await repo.link_fact(original_key, fid_attribution, extraction_role="source_attribution")
        await repo.link_fact(original_key, fid_both, extraction_role="source_attribution")

        child_key = make_seed_key("entity", "Split Role Test (specific)")
        new_seeds = [
            {"key": child_key, "name": "Split Role Test (specific)", "node_type": "entity", "label": "specific"},
        ]
        fact_assignments = {
            child_key: [fid_mentioned, fid_attribution, fid_both],
        }

        await repo.split_seed(original_key, new_seeds, fact_assignments, reason="role test")

        # Verify child seed has facts with preserved roles
        child_facts = await repo.get_seed_facts(child_key)
        role_map = {sf.fact_id: sf.extraction_role for sf in child_facts}
        assert role_map[fid_mentioned] == "mentioned"
        assert role_map[fid_attribution] == "source_attribution"
        assert role_map[fid_both] == "source_attribution"


@pytest.mark.asyncio
class TestPhoneticSearch:
    """Tests for phonetic code storage and lookup."""

    async def test_update_and_find_by_phonetic(self, write_db_session: AsyncSession) -> None:
        repo = WriteSeedRepository(write_db_session)
        key = make_seed_key("concept", "Phonetic Test Seed")
        await repo.upsert_seed(key, "Phonetic Test Seed", "concept")
        await repo.update_phonetic_code(key, "FNTK")

        results = await repo.find_by_phonetic("FNTK", "concept")
        assert any(s.key == key for s in results)

    async def test_find_by_phonetic_filters_by_type(self, write_db_session: AsyncSession) -> None:
        repo = WriteSeedRepository(write_db_session)
        key = make_seed_key("entity", "Phonetic Type Filter")
        await repo.upsert_seed(key, "Phonetic Type Filter", "entity")
        await repo.update_phonetic_code(key, "FNTKT")

        # Wrong type should not match
        results = await repo.find_by_phonetic("FNTKT", "concept")
        assert not any(s.key == key for s in results)

        # Correct type should match
        results = await repo.find_by_phonetic("FNTKT", "entity")
        assert any(s.key == key for s in results)

    async def test_find_by_phonetic_empty_code(self, write_db_session: AsyncSession) -> None:
        repo = WriteSeedRepository(write_db_session)
        results = await repo.find_by_phonetic("", "concept")
        assert results == []

    async def test_update_context_hash(self, write_db_session: AsyncSession) -> None:
        repo = WriteSeedRepository(write_db_session)
        key = make_seed_key("concept", "Context Hash Test")
        await repo.upsert_seed(key, "Context Hash Test", "concept")
        await repo.update_context_hash(key, "abc123def456")

        seed = await repo.get_seed_by_key(key)
        assert seed is not None
        assert seed.context_hash == "abc123def456"


@pytest.mark.asyncio
class TestMakeSeedKey:
    """Tests for the make_seed_key function."""

    async def test_basic_key(self) -> None:
        assert make_seed_key("concept", "Artificial Intelligence") == "concept:artificial-intelligence"

    async def test_matches_node_key(self) -> None:
        assert make_seed_key("entity", "OpenAI") == make_node_key("entity", "OpenAI")

    async def test_deterministic_uuid(self) -> None:
        key = make_seed_key("entity", "Test")
        uuid1 = key_to_uuid(key)
        uuid2 = key_to_uuid(key)
        assert uuid1 == uuid2
