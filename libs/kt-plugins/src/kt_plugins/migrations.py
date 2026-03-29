"""Per-plugin Alembic migration runner."""

from __future__ import annotations

import logging

from kt_plugins.manifest import PluginManifest

logger = logging.getLogger(__name__)


def run_plugin_migrations(
    manifests: list[PluginManifest],
    database_url: str,
) -> None:
    """Run Alembic migrations for all plugins that declare a migration path.

    Each plugin uses its own ``alembic_version_<plugin_id>`` table
    so migrations never conflict with each other or the core schema.

    Args:
        manifests: Plugin manifests (only those with ``migration_path`` are processed).
        database_url: Database URL (async driver is replaced with sync for Alembic).
    """
    plugins_with_migrations = [m for m in manifests if m.migration_path]
    if not plugins_with_migrations:
        return

    # Alembic requires a sync driver
    sync_url = database_url.replace("+asyncpg", "").replace("+aiosqlite", "")

    try:
        from alembic import command
        from alembic.config import Config
    except ImportError:
        logger.warning("Alembic not installed — skipping plugin migrations")
        return

    for manifest in plugins_with_migrations:
        version_table = f"alembic_version_{manifest.id}"
        logger.info(
            "Running migrations for plugin %r (version_table=%s, path=%s)",
            manifest.id,
            version_table,
            manifest.migration_path,
        )
        try:
            cfg = Config()
            cfg.set_main_option("script_location", manifest.migration_path)
            cfg.set_main_option("sqlalchemy.url", sync_url)
            cfg.set_main_option("version_table", version_table)
            command.upgrade(cfg, "head")
            logger.info("Migrations complete for plugin %r", manifest.id)
        except Exception:
            logger.exception("Failed to run migrations for plugin %r", manifest.id)
