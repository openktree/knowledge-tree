"""Extension registry — accumulates plugin contributions during registration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class RouteRegistration:
    """A FastAPI router contributed by a plugin."""

    router: Any  # fastapi.APIRouter
    auth_required: bool = True
    prefix: str = ""
    plugin_id: str = ""


@dataclass
class ProviderRegistration:
    """A KnowledgeProvider factory contributed by a plugin."""

    factory: Callable[..., Any]
    plugin_id: str = ""


@dataclass
class AuthBackendRegistration:
    """An auth backend factory contributed by a plugin."""

    factory: Callable[..., Any]
    plugin_id: str = ""


@dataclass
class WorkflowRegistration:
    """A Hatchet workflow contributed by a plugin."""

    workflow: Any
    plugin_id: str = ""


class ExtensionRegistry:
    """Accumulates plugin contributions during the registration phase.

    Plugins call ``add_*`` methods during their ``register()`` lifecycle
    phase. The ``PluginManager`` later consumes these registrations to
    wire them into the running application.
    """

    def __init__(self) -> None:
        self._routes: list[RouteRegistration] = []
        self._providers: list[ProviderRegistration] = []
        self._auth_backends: list[AuthBackendRegistration] = []
        self._workflows: list[WorkflowRegistration] = []
        self._node_types: dict[str, dict[str, Any]] = {}
        self._fact_types: dict[str, dict[str, Any]] = {}

    # -- Routes ----------------------------------------------------------------

    def add_routes(
        self,
        router: Any,
        *,
        auth_required: bool = True,
        prefix: str = "",
        plugin_id: str = "",
    ) -> None:
        """Register a FastAPI APIRouter to be mounted at startup."""
        self._routes.append(
            RouteRegistration(
                router=router,
                auth_required=auth_required,
                prefix=prefix,
                plugin_id=plugin_id,
            )
        )

    def get_routes(self) -> list[RouteRegistration]:
        return list(self._routes)

    # -- Providers -------------------------------------------------------------

    def add_provider(
        self,
        factory: Callable[..., Any],
        *,
        plugin_id: str = "",
    ) -> None:
        """Register a KnowledgeProvider factory."""
        self._providers.append(ProviderRegistration(factory=factory, plugin_id=plugin_id))

    def get_providers(self) -> list[ProviderRegistration]:
        return list(self._providers)

    # -- Auth backends ---------------------------------------------------------

    def add_auth_backend(
        self,
        factory: Callable[..., Any],
        *,
        plugin_id: str = "",
    ) -> None:
        """Register an authentication backend factory (SSO, OIDC, etc.)."""
        self._auth_backends.append(AuthBackendRegistration(factory=factory, plugin_id=plugin_id))

    def get_auth_backends(self) -> list[AuthBackendRegistration]:
        return list(self._auth_backends)

    # -- Workflows -------------------------------------------------------------

    def add_workflow(
        self,
        workflow: Any,
        *,
        plugin_id: str = "",
    ) -> None:
        """Register a Hatchet workflow class/function."""
        self._workflows.append(WorkflowRegistration(workflow=workflow, plugin_id=plugin_id))

    def get_workflows(self) -> list[WorkflowRegistration]:
        return list(self._workflows)

    # -- Custom types ----------------------------------------------------------

    def add_node_type(self, type_id: str, schema: dict[str, Any] | None = None) -> None:
        """Register a custom node type."""
        self._node_types[type_id] = schema or {}

    def add_fact_type(self, type_id: str, schema: dict[str, Any] | None = None) -> None:
        """Register a custom fact type."""
        self._fact_types[type_id] = schema or {}

    def get_node_types(self) -> dict[str, dict[str, Any]]:
        return dict(self._node_types)

    def get_fact_types(self) -> dict[str, dict[str, Any]]:
        return dict(self._fact_types)
