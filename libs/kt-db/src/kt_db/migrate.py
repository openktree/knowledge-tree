"""Multi-graph migration runner.

Runs Alembic migrations for all registered graphs. Each graph's schema
gets its own ``alembic_version`` table via the ``ALEMBIC_SCHEMA`` env var.

All migrations run in-process via the ``PluginDatabase`` contract — no
subprocess spawn. Both the CLI entry point and ``run_startup_migrations``
call into ``migrate_all_graphs``.

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

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from kt_config.settings import get_settings
from kt_db.models import Graph
from kt_db.startup import ensure_graph_schema_migrated

logger = logging.getLogger(__name__)


async def migrate_all_graphs() -> None:
    """Run Alembic migrations for every active graph.

    1. Connect to the control-plane graph-db (public schema)
    2. Load all active Graph rows
    3. For each graph whose schema exists on the system graph-db / write-db,
       run migrations in-process for that schema.

    Graphs whose schemas live in a separate physical database (via
    ``database_connection_id`` → ``graph_databases`` config) are skipped
    here — those must be migrated on their own physical URL and are
    already covered by core migrations on each physical DB.
    """
    settings = get_settings()

    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        result = await session.execute(select(Graph).where(Graph.status == "active"))
        graphs = list(result.scalars().all())

    async with engine.connect() as conn:
        rows = await conn.execute(text("SELECT schema_name FROM information_schema.schemata"))
        graph_db_schemas = {row[0] for row in rows}
    await engine.dispose()

    write_engine = create_async_engine(settings.write_database_url, echo=False)
    async with write_engine.connect() as conn:
        rows = await conn.execute(text("SELECT schema_name FROM information_schema.schemata"))
        write_db_schemas = {row[0] for row in rows}
    await write_engine.dispose()

    logger.info("Found %d active graph(s) to migrate", len(graphs))

    for graph in graphs:
        schema = graph.schema_name
        if schema == "public":
            # Public schema is owned by the core migrations, not per-graph.
            continue

        graph_has_it = schema in graph_db_schemas
        write_has_it = schema in write_db_schemas

        if not graph_has_it and not write_has_it:
            logger.info(
                "Skipping graph '%s' — schema '%s' not found on system DBs (lives elsewhere?)",
                graph.slug,
                schema,
            )
            continue

        # Only migrate the schemas that actually exist on the system DBs.
        # ``ensure_graph_schema_migrated`` assumes both exist, so call the
        # primitives directly when only one is present.
        if graph_has_it and write_has_it:
            logger.info("Migrating graph '%s' (schema=%s)", graph.slug, schema)
            await ensure_graph_schema_migrated(
                schema,
                graph_db_url=settings.database_url,
                write_db_url=settings.write_database_url,
            )
        else:
            from kt_config.plugin import PluginDatabase
            from kt_db.core_plugin import (
                CORE_GRAPH_DB_ALEMBIC_INI,
                CORE_WRITE_DB_ALEMBIC_INI,
            )

            if graph_has_it:
                pd = PluginDatabase(
                    plugin_id=f"core-graph-db/{graph.slug}",
                    schema_name=schema,
                    alembic_config_path=CORE_GRAPH_DB_ALEMBIC_INI,
                    target="graph",
                    schema_env_var="ALEMBIC_SCHEMA",
                )
                logger.info("Migrating graph '%s' (schema=%s) graph-db only", graph.slug, schema)
                await pd.ensure_migrated(settings.database_url, schema=schema)
            if write_has_it:
                pd = PluginDatabase(
                    plugin_id=f"core-write-db/{graph.slug}",
                    schema_name=schema,
                    alembic_config_path=CORE_WRITE_DB_ALEMBIC_INI,
                    target="write",
                    schema_env_var="ALEMBIC_SCHEMA",
                )
                logger.info("Migrating graph '%s' (schema=%s) write-db only", graph.slug, schema)
                await pd.ensure_migrated(settings.write_database_url, schema=schema)

    logger.info("All graph migrations complete")


def main() -> None:
    """CLI entry point — runs core + plugin + per-graph migrations."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from kt_db.startup import run_startup_migrations

    settings = get_settings()
    asyncio.run(run_startup_migrations(settings))


if __name__ == "__main__":
    main()
