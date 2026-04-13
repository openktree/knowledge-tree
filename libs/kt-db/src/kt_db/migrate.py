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

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import kt_db
from kt_config.settings import get_settings
from kt_db.models import Graph

logger = logging.getLogger(__name__)

_KT_DB_ROOT = Path(kt_db.__file__).resolve().parents[2]  # kt_db/__init__.py -> src/kt_db -> kt-db/


async def migrate_all_graphs() -> None:
    """Run Alembic migrations for every active graph.

    1. Connect to the control-plane graph-db (public schema)
    2. Load all Graph rows
    3. For each graph whose schema exists in the target database, run
       ``alembic upgrade heads`` for both graph-db and write-db, setting
       ALEMBIC_SCHEMA to target the correct schema.

    Graphs whose schemas live in a separate database (e.g. shared-db)
    are skipped — those must be migrated via their own DATABASE_URL.
    """
    settings = get_settings()

    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        result = await session.execute(select(Graph).where(Graph.status == "active"))
        graphs = list(result.scalars().all())

    # Check which schemas actually exist in the graph-db
    graph_db_schemas: set[str] = set()
    async with engine.connect() as conn:
        rows = await conn.execute(
            select(text("schema_name")).select_from(text("information_schema.schemata"))
        )
        graph_db_schemas = {row[0] for row in rows}

    await engine.dispose()

    # Same check for write-db
    write_engine = create_async_engine(settings.write_database_url, echo=False)
    write_db_schemas: set[str] = set()
    async with write_engine.connect() as conn:
        rows = await conn.execute(
            select(text("schema_name")).select_from(text("information_schema.schemata"))
        )
        write_db_schemas = {row[0] for row in rows}
    await write_engine.dispose()

    logger.info("Found %d active graph(s) to migrate", len(graphs))

    for graph in graphs:
        schema = graph.schema_name
        env = {**os.environ}
        if schema != "public":
            env["ALEMBIC_SCHEMA"] = schema

        # Run graph-db migrations (only if schema exists)
        if schema in graph_db_schemas:
            logger.info("Migrating graph '%s' (schema=%s) graph-db", graph.slug, schema)
            _run_alembic("alembic.ini", "alembic", env, graph.slug, "graph-db")
        else:
            logger.info("Skipping graph '%s' graph-db — schema '%s' not found (lives in another database?)", graph.slug, schema)

        # Run write-db migrations (only if schema exists)
        if schema in write_db_schemas:
            logger.info("Migrating graph '%s' (schema=%s) write-db", graph.slug, schema)
            _run_alembic("alembic_write.ini", "alembic_write", env, graph.slug, "write-db")
        else:
            logger.info("Skipping graph '%s' write-db — schema '%s' not found (lives in another database?)", graph.slug, schema)

    logger.info("All graph migrations complete")


def _run_alembic(
    ini_file: str,
    script_location: str,
    env: dict[str, str],
    graph_slug: str,
    db_label: str,
) -> None:
    """Run alembic upgrade heads for a specific config."""
    cmd = [
        sys.executable,
        "-m",
        "alembic",
        "-c",
        str(_KT_DB_ROOT / ini_file),
        "upgrade",
        "heads",
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
