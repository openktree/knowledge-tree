"""FastAPI application entry point."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from kt_api.router import api_router

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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler for startup/shutdown."""
    # Startup
    from pathlib import Path

    from fastapi import Depends

    from kt_api.auth.tokens import require_auth
    from kt_api.dependencies import get_session_factory_cached, get_write_session_factory_cached
    from kt_config.settings import get_settings
    from kt_plugins.manager import PluginManager

    settings = get_settings()
    Path(settings.ingest_upload_dir).mkdir(parents=True, exist_ok=True)

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

    # Plugin system
    plugin_manager = PluginManager()
    try:
        await plugin_manager.initialize(
            enabled_plugins=settings.enabled_plugins or None,
            license_keys=settings.plugin_license_keys,
        )
        await plugin_manager.bootstrap(
            session_factory=get_session_factory_cached(),
            write_session_factory=get_write_session_factory_cached(),
        )
        # Wire plugin routes into the API
        for route_reg in plugin_manager.get_plugin_routes():
            prefix = f"/api/v1/plugins/{route_reg.prefix}" if route_reg.prefix else ""
            deps = [Depends(require_auth)] if route_reg.auth_required else []
            api_router.include_router(route_reg.router, prefix=prefix, dependencies=deps)
    except Exception:
        logger.warning("Plugin system initialization failed", exc_info=True)

    app.state.plugin_manager = plugin_manager
    app.state.hook_registry = plugin_manager.hook_registry

    yield
    # Shutdown
    await plugin_manager.shutdown()

    from kt_api.dependencies import reset_session_factory

    reset_session_factory()


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

    app.include_router(api_router)
    return app


app = create_app()
