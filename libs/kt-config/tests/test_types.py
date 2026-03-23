"""Unit tests for shared/types.py helpers."""

from uuid import UUID

from kt_config.types import UNDIRECTED_EDGE_TYPES, canonicalize_edge_ids

# Two fixed UUIDs where SMALL < BIG lexicographically
SMALL = UUID("00000000-0000-0000-0000-000000000001")
BIG = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")


class TestCanonicalizeEdgeIds:
    """Tests for canonicalize_edge_ids()."""

    # -- all edges are undirected now, always canonical --

    def test_related_already_canonical(self) -> None:
        src, tgt = canonicalize_edge_ids(SMALL, BIG, "related")
        assert src == SMALL
        assert tgt == BIG

    def test_related_swaps_when_reversed(self) -> None:
        src, tgt = canonicalize_edge_ids(BIG, SMALL, "related")
        assert src == SMALL
        assert tgt == BIG

    def test_unknown_type_preserves_order(self) -> None:
        """Unknown types are treated as directed — order is preserved."""
        src, tgt = canonicalize_edge_ids(BIG, SMALL, "unknown_type")
        assert src == BIG
        assert tgt == SMALL

    def test_draws_from_preserves_order(self) -> None:
        """draws_from is directed — order is preserved."""
        src, tgt = canonicalize_edge_ids(BIG, SMALL, "draws_from")
        assert src == BIG
        assert tgt == SMALL

    # -- same UUID (self-edge) ------------------------------------------

    def test_same_uuid_is_noop(self) -> None:
        src, tgt = canonicalize_edge_ids(SMALL, SMALL, "related")
        assert src == SMALL
        assert tgt == SMALL

    # -- undirected set completeness ------------------------------------

    def test_undirected_types_contains_expected(self) -> None:
        assert UNDIRECTED_EDGE_TYPES == {"related", "cross_type"}
