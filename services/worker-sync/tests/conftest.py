"""Shared fixtures for worker-sync integration tests.

Mirrors the dual-DB schema-per-worker pattern from libs/kt-db/tests/conftest.py
so we can stand up a real SyncEngine against isolated graph-db + write-db
schemas.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator

os.environ.setdefault("USE_HATCHET", "false")
os.environ.setdefault("SKIP_AUTH", "true")
# The dedup workflow module imports the Hatchet client at import time,
# which refuses to initialise without a token. Unit-level tests only
# touch pure helpers, so any opaque placeholder is sufficient.
os.environ.setdefault(
    "HATCHET_CLIENT_TOKEN",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJkdW1teSJ9.dummy",
)
os.environ.setdefault("HATCHET_CLIENT_HOST_PORT", "localhost:7070")
os.environ.setdefault("HATCHET_CLIENT_TLS_STRATEGY", "none")

import pytest
import pytest_asyncio
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from kt_config.settings import get_settings
from kt_db.models import Base
from kt_db.write_models import WriteBase


def _worker_schema() -> str:
    return f"test_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture(scope="session")
def graph_schema_name():
    return _worker_schema()


@pytest.fixture(scope="session")
def write_schema_name():
    return _worker_schema()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def graph_engine(settings, graph_schema_name) -> AsyncGenerator[AsyncEngine, None]:
    base_url = settings.database_url
    setup_eng = create_async_engine(base_url, echo=False)
    async with setup_eng.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {graph_schema_name}"))
        await conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('create_extensions'))"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    await setup_eng.dispose()

    eng = create_async_engine(
        base_url,
        echo=False,
        connect_args={"server_settings": {"search_path": f"{graph_schema_name},public"}},
    )
    async with eng.begin() as conn:
        for table in Base.metadata.sorted_tables:
            table.schema = graph_schema_name
        await conn.run_sync(Base.metadata.create_all)
        for table in Base.metadata.sorted_tables:
            table.schema = None
    yield eng
    await eng.dispose()
    cleanup_eng = create_async_engine(base_url, echo=False)
    async with cleanup_eng.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {graph_schema_name} CASCADE"))
    await cleanup_eng.dispose()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def write_engine(settings, write_schema_name) -> AsyncGenerator[AsyncEngine, None]:
    base_url = settings.write_database_url
    setup_eng = create_async_engine(base_url, echo=False)
    async with setup_eng.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {write_schema_name}"))
        await conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('create_extensions'))"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    await setup_eng.dispose()

    eng = create_async_engine(base_url, echo=False)

    @event.listens_for(eng.sync_engine, "connect")
    def _set_search_path(dbapi_conn, connection_record):  # type: ignore[no-untyped-def]
        cursor = dbapi_conn.cursor()
        cursor.execute(f"SET search_path TO {write_schema_name}, public")
        cursor.close()

    async with eng.begin() as conn:
        for table in WriteBase.metadata.sorted_tables:
            table.schema = write_schema_name
        await conn.run_sync(WriteBase.metadata.create_all)
        for table in WriteBase.metadata.sorted_tables:
            table.schema = None
    yield eng
    await eng.dispose()
    cleanup_eng = create_async_engine(base_url, echo=False)
    async with cleanup_eng.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {write_schema_name} CASCADE"))
    await cleanup_eng.dispose()


@pytest.fixture(scope="session")
def graph_session_factory(graph_engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(graph_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(scope="session")
def write_session_factory(write_engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(write_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
def sync_engine(write_session_factory, graph_session_factory):
    """Real SyncEngine wired to per-worker isolated schemas."""
    from kt_worker_sync.sync_engine import SyncEngine

    return SyncEngine(
        write_session_factory=write_session_factory,
        graph_session_factory=graph_session_factory,
        batch_size=100,
    )
