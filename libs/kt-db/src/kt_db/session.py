import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from kt_config.settings import get_settings

logger = logging.getLogger(__name__)

_SUPPORTED_SSLMODES = {"require", "verify-ca", "verify-full"}


def _ssl_connect_args(sslmode: str) -> dict:
    """Build asyncpg ssl connect_args from a PostgreSQL sslmode string."""
    if not sslmode:
        return {}
    import ssl as _ssl

    if sslmode not in _SUPPORTED_SSLMODES:
        logger.warning(
            "Unrecognised db_sslmode=%r — ignoring (supported: %s)",
            sslmode,
            ", ".join(sorted(_SUPPORTED_SSLMODES)),
        )
        return {}

    if sslmode == "require":
        ctx = _ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE
        return {"ssl": ctx}
    # verify-ca / verify-full
    ctx = _ssl.create_default_context()
    if sslmode == "verify-ca":
        ctx.check_hostname = False
    return {"ssl": ctx}


def get_engine(
    database_url: str | None = None,
    pool_size: int | None = None,
    max_overflow: int | None = None,
    pool_timeout: int | None = None,
    application_name: str = "kt",
):
    settings = get_settings()
    url = database_url or settings.database_url
    connect_args: dict = {
        "statement_cache_size": 0,
        "server_settings": {"application_name": application_name},
        **_ssl_connect_args(settings.db_sslmode),
    }
    return create_async_engine(
        url,
        echo=False,
        pool_size=pool_size if pool_size is not None else settings.db_pool_size,
        max_overflow=max_overflow if max_overflow is not None else settings.db_max_overflow,
        pool_timeout=pool_timeout if pool_timeout is not None else settings.db_pool_timeout,
        pool_pre_ping=True,
        pool_recycle=settings.db_pool_recycle,
        connect_args=connect_args,
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
    connect_args: dict = {
        "statement_cache_size": 0,
        "server_settings": {"application_name": application_name},
        **_ssl_connect_args(settings.write_db_sslmode or settings.db_sslmode),
    }
    return create_async_engine(
        url,
        echo=False,
        pool_size=pool_size if pool_size is not None else settings.write_db_pool_size,
        max_overflow=max_overflow if max_overflow is not None else settings.write_db_max_overflow,
        pool_timeout=pool_timeout if pool_timeout is not None else settings.write_db_pool_timeout,
        pool_pre_ping=True,
        pool_recycle=settings.write_db_pool_recycle,
        connect_args=connect_args,
    )


def get_write_session_factory(
    database_url: str | None = None,
    application_name: str = "kt",
) -> async_sessionmaker[AsyncSession]:
    """Create a session factory for the write-optimized database."""
    engine = get_write_engine(database_url, application_name=application_name)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
