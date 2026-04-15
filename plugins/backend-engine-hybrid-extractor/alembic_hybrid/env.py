"""Alembic env.py for the backend-engine-hybrid-extractor plugin schema.

Targets the write-db. All tables live in the ``plugin_hybrid_extractor`` schema.
The alembic_version table also lives in that schema (version_table_schema).
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import MetaData, pool, text
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Plugin schema name — all tables and the version table live here.
_PLUGIN_SCHEMA = "plugin_hybrid_extractor"

# Use a standalone MetaData for autogenerate; the plugin only uses raw SQL
# migrations so we don't need ORM models here.
target_metadata = MetaData(schema=_PLUGIN_SCHEMA)

# Allow URL override from caller (PluginDatabase.ensure_migrated passes it via
# config.set_main_option before calling command.upgrade).
_db_url = config.get_main_option("sqlalchemy.url")
if _db_url:
    config.set_main_option("sqlalchemy.url", _db_url)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema=_PLUGIN_SCHEMA,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {_PLUGIN_SCHEMA}"))
    connection.execute(text(f"SET search_path TO {_PLUGIN_SCHEMA}, public"))
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema=_PLUGIN_SCHEMA,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"statement_cache_size": 0},
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
