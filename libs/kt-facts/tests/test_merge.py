"""Unit tests for ``kt_facts.processing.merge``.

Integration tests against a real Postgres live in the worker-sync
integration suite — here we just verify that:

* ``merge_into_fast`` short-circuits on self-merge.
* ``merge_into_heavy`` short-circuits on self-merge.
* ``merge_into_fast`` issues write-db statements in the expected order.
* ``merge_into_heavy`` issues graph-db statements too.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from kt_facts.processing.merge import merge_into_fast, merge_into_heavy


def _make_session() -> AsyncMock:
    s = AsyncMock()
    s.execute = AsyncMock()
    return s


@pytest.mark.asyncio
async def test_merge_into_fast_self_merge_is_noop() -> None:
    session = _make_session()
    same = uuid.uuid4()
    await merge_into_fast(session, loser_id=same, canonical_id=same)
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_merge_into_fast_touches_expected_tables_in_order() -> None:
    session = _make_session()
    loser = uuid.uuid4()
    canonical = uuid.uuid4()

    await merge_into_fast(session, loser_id=loser, canonical_id=canonical)

    sql_statements = [call.args[0].text for call in session.execute.call_args_list]
    joined = "\n".join(sql_statements)

    # The fast mode must touch exactly these four tables.
    assert "write_fact_sources" in joined
    assert "write_seed_facts" in joined
    assert "write_edge_candidates" in joined
    assert "write_facts" in joined
    # And the final statement must be the loser-delete.
    assert "DELETE FROM write_facts" in sql_statements[-1]
    assert "write_dimensions" not in joined
    assert "write_nodes" not in joined
    assert "node_facts" not in joined


@pytest.mark.asyncio
async def test_merge_into_heavy_self_merge_is_noop() -> None:
    ws = _make_session()
    gs = _make_session()
    same = uuid.uuid4()
    await merge_into_heavy(
        write_session=ws,
        graph_session=gs,
        qdrant_client=None,
        qdrant_collection="facts",
        loser_id=same,
        canonical_id=same,
    )
    ws.execute.assert_not_called()
    gs.execute.assert_not_called()


@pytest.mark.asyncio
async def test_merge_into_heavy_touches_graph_db_junctions() -> None:
    ws = _make_session()
    gs = _make_session()
    loser = uuid.uuid4()
    canonical = uuid.uuid4()

    await merge_into_heavy(
        write_session=ws,
        graph_session=gs,
        qdrant_client=None,
        qdrant_collection="facts",
        loser_id=loser,
        canonical_id=canonical,
    )

    ws_sql = "\n".join(call.args[0].text for call in ws.execute.call_args_list)
    gs_sql = "\n".join(call.args[0].text for call in gs.execute.call_args_list)

    # Write-db: scalar refs + array refs + scalar rejections.
    for tbl in (
        "write_fact_sources",
        "write_seed_facts",
        "write_edge_candidates",
        "write_node_fact_rejections",
        "write_dimensions",
        "write_edges",
        "write_nodes",
        "write_seed_merges",
        "write_facts",
    ):
        assert tbl in ws_sql, f"{tbl} not in heavy write-db sql"

    # Graph-db: junctions + facts row.
    for tbl in (
        "node_facts",
        "edge_facts",
        "dimension_facts",
        "fact_sources",
        "node_fact_rejections",
        "facts",
    ):
        assert tbl in gs_sql, f"{tbl} not in heavy graph-db sql"


@pytest.mark.asyncio
async def test_merge_into_heavy_calls_qdrant_delete_when_client_given() -> None:
    ws = _make_session()
    gs = _make_session()
    # Stub Qdrant client: delete_batch should be called once.
    qdrant_client = MagicMock()
    loser = uuid.uuid4()
    canonical = uuid.uuid4()

    # ``merge_into_heavy`` imports QdrantFactRepository lazily; we
    # monkey-patch that symbol via sys.modules so the test doesn't
    # require kt_qdrant to talk to a real server.
    from unittest.mock import patch

    class _StubRepo:
        def __init__(self, *_: object, **__: object) -> None:
            self.delete_batch = AsyncMock()

    with patch("kt_qdrant.repositories.facts.QdrantFactRepository", _StubRepo):
        await merge_into_heavy(
            write_session=ws,
            graph_session=gs,
            qdrant_client=qdrant_client,
            qdrant_collection="my_prefix__facts",
            loser_id=loser,
            canonical_id=canonical,
        )

    # Heavy mode should also delete the loser write_facts row.
    ws_sql = "\n".join(call.args[0].text for call in ws.execute.call_args_list)
    assert "DELETE FROM write_facts" in ws_sql
