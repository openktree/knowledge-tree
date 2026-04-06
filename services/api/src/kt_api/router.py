"""Central API router that includes all endpoint routers."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from kt_api.admin import router as admin_router
from kt_api.auth.router import router as auth_router
from kt_api.auth.tokens import require_auth
from kt_api.config_api import router as config_router
from kt_api.conversations import router as conversations_router
from kt_api.edge_candidates import router as edge_candidates_router
from kt_api.edges import router as edges_router
from kt_api.export import router as export_router
from kt_api.facts import router as facts_router
from kt_api.graph import router as graph_router
from kt_api.graph_builder import router as graph_builder_router
from kt_api.graph_conversations import router as graph_conversations_router
from kt_api.graph_edge_candidates import router as graph_edge_candidates_router
from kt_api.graph_edges import router as graph_edges_router
from kt_api.graph_facts import router as graph_facts_router
from kt_api.graph_nodes import router as graph_nodes_router
from kt_api.graph_research import router as graph_research_router
from kt_api.graph_seeds import router as graph_seeds_router
from kt_api.graph_sources import router as graph_sources_router
from kt_api.graph_syntheses import router as graph_syntheses_router
from kt_api.graphs import router as graphs_router
from kt_api.health import router as health_router
from kt_api.import_router import router as import_router
from kt_api.invites import router as invites_router
from kt_api.members import router as members_router
from kt_api.nodes import router as nodes_router
from kt_api.progress import router as progress_router
from kt_api.prompt_transparency import router as prompts_router
from kt_api.reports import router as reports_router
from kt_api.research import router as research_router
from kt_api.seeds import router as seeds_router
from kt_api.sources import router as sources_router
from kt_api.syntheses import router as syntheses_router
from kt_api.system_settings import router as system_settings_router
from kt_api.usage import router as usage_router
from kt_api.waitlist import router as waitlist_router

_auth_dep = [Depends(require_auth)]

api_router = APIRouter()
# Health check is public (no auth)
api_router.include_router(health_router)
# Auth routes are public (login, register, etc.)
api_router.include_router(auth_router)
# All other routes require authentication
api_router.include_router(conversations_router, dependencies=_auth_dep)
api_router.include_router(nodes_router, dependencies=_auth_dep)
api_router.include_router(edges_router, dependencies=_auth_dep)
api_router.include_router(graph_router, dependencies=_auth_dep)
api_router.include_router(graphs_router, dependencies=_auth_dep)
api_router.include_router(graph_nodes_router, dependencies=_auth_dep)
api_router.include_router(graph_facts_router, dependencies=_auth_dep)
api_router.include_router(graph_edges_router, dependencies=_auth_dep)
api_router.include_router(graph_sources_router, dependencies=_auth_dep)
api_router.include_router(graph_conversations_router, dependencies=_auth_dep)
api_router.include_router(graph_seeds_router, dependencies=_auth_dep)
api_router.include_router(graph_edge_candidates_router, dependencies=_auth_dep)
api_router.include_router(graph_syntheses_router, dependencies=_auth_dep)
api_router.include_router(graph_research_router, dependencies=_auth_dep)
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
api_router.include_router(syntheses_router, dependencies=_auth_dep)
api_router.include_router(progress_router, dependencies=_auth_dep)
api_router.include_router(reports_router, dependencies=_auth_dep)
api_router.include_router(system_settings_router, dependencies=_auth_dep)
# Waitlist + invites: public + admin (auth enforced per-endpoint)
api_router.include_router(waitlist_router)
api_router.include_router(invites_router)
# Prompt transparency is public — supports research credibility
api_router.include_router(prompts_router)
