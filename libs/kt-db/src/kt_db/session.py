from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from kt_config.settings import get_settings


def get_engine(
    database_url: str | None = None,
    pool_size: int | None = None,
    max_overflow: int | None = None,
    pool_timeout: int | None = None,
    application_name: str = "kt",
):
    settings = get_settings()
    url = database_url or settings.database_url
    return create_async_engine(
        url,
        echo=False,
        pool_size=pool_size if pool_size is not None else settings.db_pool_size,
        max_overflow=max_overflow if max_overflow is not None else settings.db_max_overflow,
        pool_timeout=pool_timeout if pool_timeout is not None else settings.db_pool_timeout,
        pool_pre_ping=True,
        pool_recycle=settings.db_pool_recycle,
        connect_args={
            "statement_cache_size": 0,
            "server_settings": {"application_name": application_name},
        },
    )


def get_session_factory(
    database_url: str | None = None,
    application_name: str = "kt",
) -> async_sessionmaker[AsyncSession]:
    engine = get_engine(database_url, application_name=application_name)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def get_write_engine(
    database_url: str | None = None,
    pool_size: int | None = None,
    max_overflow: int | None = None,
    pool_timeout: int | None = None,
    application_name: str = "kt",
):
    """Create an engine for the write-optimized database."""
    settings = get_settings()
    url = database_url or settings.write_database_url
    return create_async_engine(
        url,
        echo=False,
        pool_size=pool_size if pool_size is not None else settings.write_db_pool_size,
        max_overflow=max_overflow if max_overflow is not None else settings.write_db_max_overflow,
        pool_timeout=pool_timeout if pool_timeout is not None else settings.write_db_pool_timeout,
        pool_pre_ping=True,
        pool_recycle=settings.write_db_pool_recycle,
        connect_args={
            "statement_cache_size": 0,
            "server_settings": {"application_name": application_name},
        },
    )


def get_write_session_factory(
    database_url: str | None = None,
    application_name: str = "kt",
) -> async_sessionmaker[AsyncSession]:
    """Create a session factory for the write-optimized database."""
    engine = get_write_engine(database_url, application_name=application_name)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
