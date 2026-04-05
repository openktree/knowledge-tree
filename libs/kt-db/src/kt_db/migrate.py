"""Multi-graph migration runner.

Runs Alembic migrations for all registered graphs. Each graph's schema
gets its own ``alembic_version`` table via the ``ALEMBIC_SCHEMA`` env var.

Usage::

    uv run --project libs/kt-db python -m kt_db.migrate

Or programmatically::

    from kt_db.migrate import migrate_all_graphs
    import asyncio
    asyncio.run(migrate_all_graphs())
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from kt_config.settings import get_settings
from kt_db.models import Graph

logger = logging.getLogger(__name__)

_KT_DB_ROOT = Path(__file__).resolve().parents[2]  # libs/kt-db/


async def migrate_all_graphs() -> None:
    """Run Alembic migrations for every active graph.

    1. Connect to the control-plane graph-db (public schema)
    2. Load all Graph rows
    3. For each graph, run ``alembic upgrade head`` for both graph-db and
       write-db, setting ALEMBIC_SCHEMA to target the correct schema.
    """
    settings = get_settings()

    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        result = await session.execute(select(Graph).where(Graph.status == "active"))
        graphs = list(result.scalars().all())

    await engine.dispose()

    logger.info("Found %d active graph(s) to migrate", len(graphs))

    for graph in graphs:
        schema = graph.schema_name
        logger.info("Migrating graph '%s' (schema=%s)", graph.slug, schema)

        env = {**os.environ}
        if schema != "public":
            env["ALEMBIC_SCHEMA"] = schema

        # Run graph-db migrations
        _run_alembic("alembic.ini", "alembic", env, graph.slug, "graph-db")

        # Run write-db migrations
        _run_alembic("alembic_write.ini", "alembic_write", env, graph.slug, "write-db")

    logger.info("All graph migrations complete")


def _run_alembic(
    ini_file: str,
    script_location: str,
    env: dict[str, str],
    graph_slug: str,
    db_label: str,
) -> None:
    """Run alembic upgrade head for a specific config."""
    cmd = [
        sys.executable,
        "-m",
        "alembic",
        "-c",
        str(_KT_DB_ROOT / ini_file),
        "upgrade",
        "head",
    ]
    logger.info("  [%s/%s] %s", graph_slug, db_label, " ".join(cmd[-3:]))
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, cwd=str(_KT_DB_ROOT))
    if result.returncode != 0:
        logger.error(
            "  [%s/%s] Migration FAILED:\n%s\n%s",
            graph_slug,
            db_label,
            result.stdout,
            result.stderr,
        )
        raise RuntimeError(f"Migration failed for graph '{graph_slug}' ({db_label})")
    logger.info("  [%s/%s] Migration OK", graph_slug, db_label)


def main() -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    asyncio.run(migrate_all_graphs())


if __name__ == "__main__":
    main()
