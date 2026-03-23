"""Central API router that includes all endpoint routers."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from kt_api.admin import router as admin_router
from kt_api.members import router as members_router
from kt_api.system_settings import router as system_settings_router
from kt_api.edge_candidates import router as edge_candidates_router
from kt_api.graph_builder import router as graph_builder_router
from kt_api.config_api import router as config_router
from kt_api.conversations import router as conversations_router
from kt_api.edges import router as edges_router
from kt_api.export import router as export_router
from kt_api.facts import router as facts_router
from kt_api.graph import router as graph_router
from kt_api.import_router import router as import_router
from kt_api.research import router as research_router
from kt_api.nodes import router as nodes_router
from kt_api.seeds import router as seeds_router
from kt_api.sources import router as sources_router
from kt_api.usage import router as usage_router
from kt_api.auth.router import router as auth_router
from kt_api.auth.tokens import require_auth

_auth_dep = [Depends(require_auth)]

api_router = APIRouter()
# Auth routes are public (login, register, etc.)
api_router.include_router(auth_router)
# All other routes require authentication
api_router.include_router(conversations_router, dependencies=_auth_dep)
api_router.include_router(nodes_router, dependencies=_auth_dep)
api_router.include_router(edges_router, dependencies=_auth_dep)
api_router.include_router(graph_router, dependencies=_auth_dep)
api_router.include_router(facts_router, dependencies=_auth_dep)
api_router.include_router(sources_router, dependencies=_auth_dep)
api_router.include_router(config_router, dependencies=_auth_dep)
api_router.include_router(admin_router, dependencies=_auth_dep)
api_router.include_router(export_router, dependencies=_auth_dep)
api_router.include_router(import_router, dependencies=_auth_dep)
api_router.include_router(research_router, dependencies=_auth_dep)
api_router.include_router(seeds_router, dependencies=_auth_dep)
api_router.include_router(edge_candidates_router, dependencies=_auth_dep)
api_router.include_router(graph_builder_router, dependencies=_auth_dep)
api_router.include_router(usage_router, dependencies=_auth_dep)
api_router.include_router(members_router, dependencies=_auth_dep)
api_router.include_router(system_settings_router, dependencies=_auth_dep)
