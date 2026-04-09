"""Pure helpers for the contribute-retry sweeper.

Lives outside ``workflows/`` so unit tests can import it without
triggering ``get_hatchet()`` (which requires a Hatchet token in the
environment). The Hatchet workflow in
``workflows/public_cache_sweeper.py`` is a thin shell around these
helpers.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from kt_db.write_models import WriteRawSource

if TYPE_CHECKING:
    from kt_hatchet.lifespan import WorkerState

logger = logging.getLogger(__name__)


async def sweep_one_graph(
    state: WorkerState,
    *,
    graph_id,
    graph_slug: str,
    write_session_factory,
    qdrant_collection_prefix: str,
    cutoff: datetime,
    batch_size: int,
) -> tuple[int, int]:
    """Process up to ``batch_size`` straggler rows for one graph.

    Returns ``(succeeded, failed)``. Per-row failures are caught so a
    single bad row doesn't poison the rest of the batch. The bridge
    already swallows + logs its own write failures and stamps the
    watermark only on success — so the sweeper decides ok/fail by
    re-reading the watermark column after each contribute call.
    """
    succeeded = 0
    failed = 0

    async with write_session_factory() as write_session:
        # Find candidate rows. Mirrors the partial index in PR7's
        # write-db migration: only link sources (canonical_url IS NOT
        # NULL — file uploads never have one), no watermark yet, and
        # old enough to be safely past the in-flight ingest window.
        stmt = (
            select(WriteRawSource.id)
            .where(
                WriteRawSource.contributed_to_public_at.is_(None),
                WriteRawSource.canonical_url.isnot(None),
                WriteRawSource.created_at < cutoff,
            )
            .order_by(WriteRawSource.created_at.asc())
            .limit(batch_size)
        )
        try:
            result = await write_session.execute(stmt)
            candidate_ids = [row[0] for row in result.all()]
        except Exception:
            logger.warning(
                "public_cache_sweep: candidate query failed for graph %s",
                graph_slug,
                exc_info=True,
            )
            return (0, 0)

        if not candidate_ids:
            return (0, 0)

        logger.info(
            "public_cache.sweep_start graph=%s candidates=%d",
            graph_slug,
            len(candidate_ids),
        )

        # Build a worker engine wired to this graph's bridge. The
        # factory raises if the qdrant prefix is empty for a non-default
        # graph — that's defence in depth from PR4 and we let it
        # propagate as a hard failure since something is seriously
        # mis-wired if it triggers here.
        engine = state.make_worker_engine(
            write_session,
            graph_id=graph_id,
            qdrant_collection_prefix=qdrant_collection_prefix,
        )

        if not engine.has_public_bridge:
            # Belt-and-suspenders: the workflow shell already filters
            # default graphs out, so reaching here means something
            # mis-wired the factory. Don't loop over candidates with a
            # no-op engine.
            logger.debug("public_cache_sweep: no bridge for graph %s", graph_slug)
            return (0, 0)

        for raw_id in candidate_ids:
            try:
                await engine.contribute_to_public(raw_source_id=raw_id)
            except Exception:
                logger.warning(
                    "public_cache.sweep_retry_unhandled graph=%s raw_source_id=%s",
                    graph_slug,
                    raw_id,
                    exc_info=True,
                )
                failed += 1
                continue

            # Re-read the watermark to decide whether the contribute
            # actually landed. The next sweep retries unstamped rows.
            try:
                stamped = await write_session.execute(
                    select(WriteRawSource.contributed_to_public_at).where(WriteRawSource.id == raw_id)
                )
                if stamped.scalar_one_or_none() is not None:
                    succeeded += 1
                else:
                    failed += 1
            except Exception:
                logger.warning(
                    "public_cache.sweep_watermark_check_failed graph=%s raw_source_id=%s",
                    graph_slug,
                    raw_id,
                    exc_info=True,
                )
                failed += 1

        # Commit watermark stamps. The bridge runs the stamp UPDATE
        # against the source session but does NOT commit — that's our
        # job, and we do it once per graph rather than once per row to
        # keep transaction churn low.
        try:
            await write_session.commit()
        except Exception:
            logger.warning(
                "public_cache_sweep: commit failed for graph %s — watermarks may roll back",
                graph_slug,
                exc_info=True,
            )

    return (succeeded, failed)
