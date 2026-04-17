"""FastAPI application entry point."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from kt_api.router import api_router
from kt_flags.fastapi import install_middleware as install_flag_middleware

logger = logging.getLogger(__name__)


def _get_cors_origins() -> list[str]:
    origins = os.environ.get("CORS_ORIGINS", "")
    if origins:
        return [o.strip() for o in origins.split(",") if o.strip()]
    return ["*"]


# Suppress noisy LiteLLM debug output ("Provider List: ..." on every call)
logging.getLogger("LiteLLM").setLevel(logging.WARNING)
logging.getLogger("LiteLLM Router").setLevel(logging.WARNING)
logging.getLogger("LiteLLM Proxy").setLevel(logging.WARNING)

try:
    import litellm

    litellm.suppress_debug_info = True
except Exception:
    pass


def _register_core_db_plugins() -> None:
    """Enrol the core graph-db + write-db migration plugins.

    kt-db owns the core schemas; the plugin framework treats them the
    same as third-party plugins so ``run_startup_migrations`` has one
    iteration path. Registration happens at module import (before
    ``plugin_manager.initialize()`` runs) so the core DB plugins are
    present before any third-party entry-point plugins can depend on
    the schema being migrated.
    """
    from kt_db.core_plugin import register_core_plugins
    from kt_plugins import plugin_manager

    register_core_plugins(plugin_manager)


_register_core_db_plugins()


async def _bootstrap_plugins(app: FastAPI) -> None:
    """Run each plugin's bootstrap phase and mount contributed routes.

    Plugins subscribe to hooks here (``ctx.hook_registry.register(...)``)
    and receive session factories + cross-cutting services via
    ``PluginContext``. Routes contributed via ``RouteContribution`` are
    mounted under ``/api/v1/plugins/{prefix}``.
    """
    from kt_api.dependencies import get_session_factory_cached, get_write_session_factory_cached
    from kt_config.settings import get_settings
    from kt_plugins import PluginContext, plugin_manager

    settings = get_settings()
    session_factory = get_session_factory_cached()
    write_session_factory = get_write_session_factory_cached()

    def _ctx_factory(manifest):  # noqa: ANN202 — runtime-typed
        plugin_settings = None
        if manifest.settings_class is not None:
            try:
                plugin_settings = manifest.settings_class()
            except Exception:
                logger.warning("plugin %r: settings failed to load", manifest.id, exc_info=True)
        return PluginContext(
            plugin_id=manifest.id,
            settings=plugin_settings or settings,
            hook_registry=plugin_manager.hook_registry,
            session_factory=session_factory,
            write_session_factory=write_session_factory,
        )

    await plugin_manager.bootstrap(ctx_factory=_ctx_factory)

    # Expose the hook registry on app.state so request-path code
    # (auth manager, middleware, route handlers) can fire hooks without
    # importing the global singleton.
    app.state.hook_registry = plugin_manager.hook_registry

    # Mount plugin routes. Done after bootstrap so plugins that build
    # their router inside bootstrap() (e.g. depending on settings) still
    # get picked up.
    from fastapi import APIRouter, Depends

    from kt_api.auth.tokens import require_auth

    plugin_root = APIRouter()
    for contrib in plugin_manager.get_plugin_routes():
        deps = [Depends(require_auth)] if contrib.auth_required else []
        plugin_root.include_router(
            contrib.router,
            prefix=f"/{contrib.prefix}" if not contrib.prefix.startswith("/") else contrib.prefix,
            dependencies=deps,
        )
    if plugin_manager.get_plugin_routes():
        app.include_router(plugin_root, prefix="/api/v1/plugins")
        logger.info(
            "mounted %d plugin route group(s) under /api/v1/plugins",
            len(plugin_manager.get_plugin_routes()),
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler for startup/shutdown."""
    # Startup
    from pathlib import Path

    from kt_config.settings import get_settings
    from kt_plugins import plugin_manager
    from kt_providers.registry import bridge_plugin_search_providers

    settings = get_settings()
    Path(settings.ingest_upload_dir).mkdir(parents=True, exist_ok=True)

    _validate_email_verification_config(settings)

    # Phase 1: discover + register plugins (entry points + legacy targets).
    # Any plugin contributing a DB schema must be registered before startup
    # migrations run below.
    await plugin_manager.initialize(
        enabled_plugins=settings.enabled_plugins or None,
        license_keys=settings.plugin_license_keys or None,
    )
    bridge_plugin_search_providers()

    # Phase 2: DB migrations — core + plugins + per-graph.
    # Guaranteed to complete before FastAPI serves any request.
    try:
        from kt_db.startup import run_startup_migrations

        await run_startup_migrations(settings)
    except Exception:
        logger.exception("Startup migrations failed — aborting API startup")
        raise

    # Phase 3: plugin bootstrap — hand runtime services + hook registry to
    # each plugin. Mounts plugin routes onto the app.
    await _bootstrap_plugins(app)

    # Ensure Qdrant collections exist
    try:
        from kt_api.dependencies import get_qdrant_client_cached
        from kt_qdrant.repositories.facts import QdrantFactRepository
        from kt_qdrant.repositories.nodes import QdrantNodeRepository

        qdrant = get_qdrant_client_cached()
        await QdrantFactRepository(qdrant).ensure_collection()
        await QdrantNodeRepository(qdrant).ensure_collection()
    except Exception:
        logger.warning("Failed to ensure Qdrant collections at startup", exc_info=True)

    # Recover graphs stuck in "provisioning" status (crash recovery)
    try:
        await _recover_stuck_graphs()
    except Exception:
        logger.warning("Failed to run graph provisioning recovery", exc_info=True)

    # Assert no DatabaseConnection row holds the reserved "default" config_key.
    # Such a row would silently shadow the synthetic system-DB entry surfaced
    # by GET /api/v1/graphs/database-connections.
    try:
        await _assert_default_db_key_unreserved()
    except Exception:
        logger.warning("Failed to validate reserved default db config_key", exc_info=True)

    yield
    # Shutdown
    from kt_api.dependencies import reset_session_factory
    from kt_plugins import plugin_manager

    await plugin_manager.shutdown()
    reset_session_factory()


def _validate_email_verification_config(settings) -> None:  # type: ignore[no-untyped-def]
    """Fail fast if verification is required but email sending is disabled.

    Locking users out with no way to receive verification emails is a silent
    data-quality disaster — refuse to start rather than let it happen.
    """
    from kt_config.errors import ConfigurationError

    if settings.email_verification_required and not (settings.email_enabled and settings.email_verification):
        raise ConfigurationError(
            "email_verification_required=true requires email_enabled=true AND "
            "email_verification=true — otherwise users cannot receive verification "
            "emails and will be locked out of login."
        )


async def _assert_default_db_key_unreserved() -> None:
    """Fail loudly at startup if a real database_connections row holds the
    reserved ``DEFAULT_DB_CONFIG_KEY``. The repository ``create`` guard
    catches new inserts; this catches anything that may have slipped in
    via raw SQL or a previous version that lacked the guard.
    """
    from sqlalchemy import select

    from kt_api.dependencies import get_session_factory_cached
    from kt_config.settings import DEFAULT_DB_CONFIG_KEY
    from kt_db.models import DatabaseConnection

    sf = get_session_factory_cached()
    async with sf() as session:
        stmt = select(DatabaseConnection.id).where(DatabaseConnection.config_key == DEFAULT_DB_CONFIG_KEY)
        result = await session.execute(stmt)
        row = result.first()
        if row is not None:
            logger.error(
                "DatabaseConnection row exists with reserved config_key=%r (id=%s); this row will be "
                "silently shadowed by the synthetic default entry. Either rename it or delete it.",
                DEFAULT_DB_CONFIG_KEY,
                row[0],
            )


async def _recover_stuck_graphs() -> None:
    """Mark graphs stuck in 'provisioning' status as 'error' on startup.

    If the API crashed during provisioning, graphs may be left in
    'provisioning' state forever. Marking them as 'error' allows admins
    to retry via the retry_provision endpoint (which is idempotent).
    """
    from kt_api.dependencies import get_session_factory_cached
    from kt_db.models import Graph

    sf = get_session_factory_cached()
    async with sf() as session:
        from sqlalchemy import update

        stmt = update(Graph).where(Graph.status == "provisioning").values(status="error").returning(Graph.slug)
        result = await session.execute(stmt)
        slugs = [row[0] for row in result.all()]
        if slugs:
            logger.warning("Found %d graph(s) stuck in provisioning: %s — marked as error", len(slugs), slugs)
        await session.commit()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title="Knowledge Tree", version="0.1.0", lifespan=lifespan)

    origins = _get_cors_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    install_flag_middleware(app)

    app.include_router(api_router)
    return app


app = create_app()
