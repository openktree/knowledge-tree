"""Startup-time migration orchestrator.

Single entry point called pre-yield from both the FastAPI API lifespan
and the Hatchet ``worker_lifespan``. Runs:

  1. Core migrations (graph-db + write-db) via registered core plugins
  2. Third-party plugin migrations (e.g. hybrid-extractor)
  3. Per-graph schema migrations (for every active non-default graph)

Core migrations run in ``strict=True`` mode — a failure aborts startup
so the service never binds with a stale schema. External plugin failures
remain best-effort.
"""

from __future__ import annotations

import logging

from kt_config.plugin import PluginDatabase, plugin_registry
from kt_config.settings import Settings
from kt_db.core_plugin import (
    CORE_GRAPH_DB_ALEMBIC_INI,
    CORE_WRITE_DB_ALEMBIC_INI,
    CoreGraphDbPlugin,
    CoreWriteDbPlugin,
)

logger = logging.getLogger(__name__)


_STALE_REVISION_HINT = (
    "\n"
    "────────────────────────────────────────────────────────────────────\n"
    "  CORE MIGRATION FAILED: %(error)s\n"
    "\n"
    "  The DB at %(url)s holds an Alembic revision that no longer exists\n"
    "  in ``%(alembic_ini)s``. This usually happens after a migration\n"
    "  squash. To recover an existing deployment, drop the stale version\n"
    "  row and stamp the new initial:\n"
    "\n"
    "    psql <url> -c 'DELETE FROM alembic_version;'\n"
    "    alembic -c %(alembic_ini)s stamp head\n"
    "\n"
    "  For per-graph schemas, also stamp each schema:\n"
    "    ALEMBIC_SCHEMA=<schema> alembic -c %(alembic_ini)s stamp head\n"
    "────────────────────────────────────────────────────────────────────"
)


async def _run_core_migration(pd: PluginDatabase, url: str) -> None:
    """Run one core migration, turning cryptic Alembic errors into
    actionable operator guidance before re-raising."""
    try:
        await pd.ensure_migrated(url)
    except Exception as exc:  # noqa: BLE001 — we re-raise after logging
        message = str(exc)
        if "Can't locate revision" in message:
            logger.error(
                _STALE_REVISION_HINT,
                {
                    "error": message,
                    "url": url,
                    "alembic_ini": pd.alembic_config_path,
                },
            )
        else:
            logger.error(
                "Core migration failed for %s (target=%s, url=%s): %s",
                pd.plugin_id,
                pd.target,
                url,
                message,
            )
        raise


def _collect_physical_urls(settings: Settings) -> tuple[list[str], list[str]]:
    """Return (graph_db_urls, write_db_urls) — system DB plus every
    non-default physical graph DB configured for this deployment."""
    graph_urls: list[str] = [settings.database_url]
    write_urls: list[str] = [settings.write_database_url]
    for cfg in settings.graph_databases.values():
        if cfg.graph_database_url and cfg.graph_database_url not in graph_urls:
            graph_urls.append(cfg.graph_database_url)
        if cfg.write_database_url and cfg.write_database_url not in write_urls:
            write_urls.append(cfg.write_database_url)
    return graph_urls, write_urls


async def run_startup_migrations(settings: Settings) -> None:
    """Run every migration needed for the service to start.

    Idempotent — Alembic only applies unreleased revisions.
    Guarded by ``settings.run_migrations_on_startup``.
    """
    if not settings.run_migrations_on_startup:
        logger.info("Startup migrations disabled (run_migrations_on_startup=False)")
        return

    graph_urls, write_urls = _collect_physical_urls(settings)

    logger.info(
        "Running startup migrations: graph_dbs=%d write_dbs=%d",
        len(graph_urls),
        len(write_urls),
    )

    # Phase 1: core migrations (strict — abort startup on failure).
    graph_core = CoreGraphDbPlugin().get_database()
    write_core = CoreWriteDbPlugin().get_database()
    for url in graph_urls:
        logger.info("  core-graph-db: %s", url)
        await _run_core_migration(graph_core, url)
    for url in write_urls:
        logger.info("  core-write-db: %s", url)
        await _run_core_migration(write_core, url)

    # Phase 2: third-party plugin migrations (best-effort — a bad plugin
    # should not block the service).
    await plugin_registry.run_database_migrations(
        write_db_urls=write_urls,
        graph_db_urls=graph_urls,
        strict=False,
    )

    # Phase 3: per-graph schemas on every physical DB where they live.
    try:
        from kt_db.migrate import migrate_all_graphs

        await migrate_all_graphs()
    except Exception:
        logger.exception("Per-graph migrations failed — aborting startup")
        raise


async def ensure_graph_schema_migrated(
    schema: str,
    *,
    graph_db_url: str,
    write_db_url: str,
) -> None:
    """Run both DB migrations for a single graph schema.

    Used by ``_provision_graph`` and by the multi-graph bulk runner.
    ``schema`` is the actual Postgres schema name (e.g. ``graph_<slug>``),
    validated by ``kt_db.keys.validate_schema_name``.
    """
    graph_db = PluginDatabase(
        plugin_id=f"core-graph-db/{schema}",
        schema_name=schema,
        alembic_config_path=CORE_GRAPH_DB_ALEMBIC_INI,
        target="graph",
        schema_env_var="ALEMBIC_SCHEMA",
    )
    write_db = PluginDatabase(
        plugin_id=f"core-write-db/{schema}",
        schema_name=schema,
        alembic_config_path=CORE_WRITE_DB_ALEMBIC_INI,
        target="write",
        schema_env_var="ALEMBIC_SCHEMA",
    )
    try:
        await graph_db.ensure_migrated(graph_db_url, schema=schema)
        await write_db.ensure_migrated(write_db_url, schema=schema)
    except Exception as exc:
        message = str(exc)
        if "Can't locate revision" in message:
            logger.error(
                "Schema '%s' migration failed: %s. "
                "Stamp the schema with:\n"
                "  ALEMBIC_SCHEMA=%s alembic -c %s stamp head\n"
                "  ALEMBIC_SCHEMA=%s alembic -c %s stamp head",
                schema,
                message,
                schema,
                CORE_GRAPH_DB_ALEMBIC_INI,
                schema,
                CORE_WRITE_DB_ALEMBIC_INI,
            )
        else:
            logger.error("Schema '%s' migration failed: %s", schema, message)
        raise
