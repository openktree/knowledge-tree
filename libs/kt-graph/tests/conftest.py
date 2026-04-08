import os
import uuid
from collections.abc import AsyncGenerator

os.environ.setdefault("USE_HATCHET", "false")
os.environ.setdefault("SKIP_AUTH", "true")

import pytest
import pytest_asyncio
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from kt_config.settings import get_settings
from kt_db.models import Base
from kt_db.write_models import WriteBase


def _worker_schema() -> str:
    return f"test_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture(scope="session")
def schema_name():
    return _worker_schema()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine(settings, schema_name) -> AsyncGenerator[AsyncEngine, None]:
    base_url = settings.database_url
    setup_eng = create_async_engine(base_url, echo=False)
    async with setup_eng.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}"))
        await conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('create_extensions'))"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    await setup_eng.dispose()

    eng = create_async_engine(
        base_url,
        echo=False,
        connect_args={"server_settings": {"search_path": f"{schema_name},public"}},
    )
    async with eng.begin() as conn:
        for table in Base.metadata.sorted_tables:
            table.schema = schema_name
        await conn.run_sync(Base.metadata.create_all)
        for table in Base.metadata.sorted_tables:
            table.schema = None
    yield eng
    await eng.dispose()
    cleanup_eng = create_async_engine(base_url, echo=False)
    async with cleanup_eng.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE"))
    await cleanup_eng.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def db_session(engine) -> AsyncGenerator[AsyncSession, None]:
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        async with session.begin():
            yield session
            await session.rollback()


# ── Write-db fixtures (used by PublicGraphBridge integration tests) ──


@pytest.fixture(scope="session")
def write_schema_name():
    return _worker_schema()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def write_engine(settings, write_schema_name) -> AsyncGenerator[AsyncEngine, None]:
    base_url = settings.write_database_url
    setup_eng = create_async_engine(base_url, echo=False)
    async with setup_eng.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {write_schema_name}"))
    await setup_eng.dispose()

    eng = create_async_engine(base_url, echo=False)

    # Write-db doesn't accept startup-time search_path (pgbouncer in
    # transaction mode rejects it). Set it on every new connection
    # instead so each session lands in the test schema.
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


@pytest_asyncio.fixture(loop_scope="session")
async def write_db_session(write_engine) -> AsyncGenerator[AsyncSession, None]:
    session_factory = async_sessionmaker(write_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        async with session.begin():
            yield session
            await session.rollback()
