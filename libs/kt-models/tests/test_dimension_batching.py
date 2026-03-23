"""Unit tests for dimension batching logic."""

from unittest.mock import MagicMock
from uuid import uuid4

from kt_worker_nodes.pipelines.dimensions.pipeline import DimensionPipeline


def _make_fact(fact_id=None):
    """Create a mock Fact with an id."""
    f = MagicMock()
    f.id = fact_id or uuid4()
    return f


def _make_dimension(batch_index=0, is_definitive=False, fact_ids=None):
    """Create a mock Dimension with dimension_facts."""
    dim = MagicMock()
    dim.id = uuid4()
    dim.batch_index = batch_index
    dim.is_definitive = is_definitive
    dim.fact_count = len(fact_ids) if fact_ids else 0

    # Build mock dimension_facts
    dim_facts = []
    for fid in (fact_ids or []):
        df = MagicMock()
        df.fact_id = fid
        dim_facts.append(df)
    dim.dimension_facts = dim_facts
    return dim


class TestBatchFacts:
    """Tests for DimensionPipeline._batch_facts()."""

    def test_no_existing_dims_single_batch(self) -> None:
        """With no existing dimensions, all facts go into batch 0."""
        facts = [_make_fact() for _ in range(10)]
        batches = DimensionPipeline._batch_facts(facts, [], fact_limit=60)
        assert len(batches) == 1
        batch_idx, batch_facts, existing = batches[0]
        assert batch_idx == 0
        assert len(batch_facts) == 10
        assert existing is None

    def test_no_existing_dims_multiple_batches(self) -> None:
        """Facts exceeding fact_limit create multiple batches."""
        facts = [_make_fact() for _ in range(25)]
        batches = DimensionPipeline._batch_facts(facts, [], fact_limit=10)
        assert len(batches) == 3
        assert len(batches[0][1]) == 10
        assert len(batches[1][1]) == 10
        assert len(batches[2][1]) == 5
        # Batch indices should be sequential from 0
        assert batches[0][0] == 0
        assert batches[1][0] == 1
        assert batches[2][0] == 2

    def test_definitive_dims_exclude_consumed_facts(self) -> None:
        """Facts consumed by definitive dimensions are excluded."""
        fact1 = _make_fact()
        fact2 = _make_fact()
        fact3 = _make_fact()
        all_facts = [fact1, fact2, fact3]

        dim = _make_dimension(
            batch_index=0,
            is_definitive=True,
            fact_ids=[fact1.id, fact2.id],
        )

        batches = DimensionPipeline._batch_facts(all_facts, [dim], fact_limit=60)
        assert len(batches) == 1
        batch_idx, batch_facts, existing = batches[0]
        assert batch_idx == 1  # Next batch index after existing dim
        assert len(batch_facts) == 1
        assert batch_facts[0].id == fact3.id

    def test_unsaturated_dim_gets_refilled(self) -> None:
        """Unsaturated draft dimensions get regenerated with new facts."""
        facts = [_make_fact() for _ in range(5)]
        draft_dim = _make_dimension(batch_index=0, is_definitive=False, fact_ids=[])

        batches = DimensionPipeline._batch_facts(facts, [draft_dim], fact_limit=60)
        assert len(batches) == 1
        batch_idx, batch_facts, existing = batches[0]
        assert batch_idx == 0  # Same as the draft dim
        assert existing is draft_dim
        assert len(batch_facts) == 5

    def test_empty_facts_no_batches(self) -> None:
        """No facts produces no batches."""
        batches = DimensionPipeline._batch_facts([], [], fact_limit=60)
        assert len(batches) == 0

    def test_all_consumed_no_batches(self) -> None:
        """If all facts are consumed by definitive dims, no batches."""
        fact1 = _make_fact()
        dim = _make_dimension(
            batch_index=0,
            is_definitive=True,
            fact_ids=[fact1.id],
        )
        batches = DimensionPipeline._batch_facts([fact1], [dim], fact_limit=60)
        assert len(batches) == 0

    def test_mixed_definitive_and_draft(self) -> None:
        """Definitive dims exclude facts; draft dim gets refilled."""
        fact1 = _make_fact()
        fact2 = _make_fact()
        fact3 = _make_fact()
        fact4 = _make_fact()

        def_dim = _make_dimension(
            batch_index=0,
            is_definitive=True,
            fact_ids=[fact1.id, fact2.id],
        )
        draft_dim = _make_dimension(batch_index=1, is_definitive=False, fact_ids=[])

        batches = DimensionPipeline._batch_facts(
            [fact1, fact2, fact3, fact4],
            [def_dim, draft_dim],
            fact_limit=60,
        )
        # fact3 and fact4 available, draft_dim at index 1 gets refilled
        assert len(batches) == 1
        batch_idx, batch_facts, existing = batches[0]
        assert batch_idx == 1
        assert existing is draft_dim
        assert len(batch_facts) == 2
        assert {f.id for f in batch_facts} == {fact3.id, fact4.id}
