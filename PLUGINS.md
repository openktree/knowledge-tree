# Knowledge Tree Plugin API

This document is the **Plugin API** referenced by the AGPL Plugin Exception in
the [LICENSE](LICENSE) (Section 7). Independent modules that communicate
**solely** through this documented API may be conveyed under terms of your
choice.

---

## Overview

A plugin is a standard Python package, discovered at startup via
`importlib.metadata` entry points. Plugins extend the platform through typed
extension points (routes, workflows, search providers, entity extractors,
post-extraction hooks, DB schemas) and an async hook bus (audit, enforcement,
usage-gating).

**Auth backends are NOT plugins.** Authentication (JWT, OAuth, and future SSO
protocols) stays in core — a third-party auth backend would be a major attack
surface for a platform whose value prop is data provenance. Plugins
integrate with auth via the `auth.*` hooks for audit, SSO group sync,
just-in-time provisioning, and SIEM forwarding.

**RBAC is NOT a plugin.** The platform ships `kt-rbac` (SystemRole, GraphRole,
per-graph `GraphMember`, source-level access groups). Plugins contribute
routes guarded by the existing `kt_rbac.Permission` values, not by a
parallel RBAC system.

---

## Plugin Package Structure

```
kt-plugin-<name>/
  pyproject.toml                      # declares entry point
  src/kt_plugin_<name>/
    __init__.py
    plugin.py                         # PluginManifest OR legacy BackendEnginePlugin
    settings.py                       # (optional) Pydantic BaseSettings
    migrations/                       # (optional) Alembic versions dir
      env.py
      versions/
  tests/
```

### Entry Point

In `pyproject.toml`:

```toml
[project.entry-points."kt.plugins"]
<name> = "kt_plugin_<name>.plugin:PluginOrManifest"
```

The target must resolve to **either**:

- a `kt_plugins.PluginManifest` instance (new API, recommended for new plugins), or
- a `kt_plugins.BackendEnginePlugin` subclass (legacy API, simpler for
  pipeline-only plugins — used by the 2 in-tree plugins).

The two styles coexist. Legacy plugins are auto-wrapped into a manifest so
both paths share one orchestrator.

---

## Two APIs

### 1. Legacy `BackendEnginePlugin` ABC

For plugins that only contribute pipeline pieces (entity extractors,
search providers, post-extraction hooks, DB schemas). No lifecycle, no
runtime context — getters are called on demand.

```python
from kt_plugins import (
    BackendEnginePlugin,
    EntityExtractorContribution,
    PluginDatabase,
)

class MyPlugin(BackendEnginePlugin):
    plugin_id = "my-plugin"

    def get_database(self) -> PluginDatabase:
        return PluginDatabase(
            plugin_id=self.plugin_id,
            schema_name="plugin_my",
            alembic_config_path=Path(...),
            target="write",           # or "graph"
        )

    def get_entity_extractors(self):
        yield EntityExtractorContribution(
            extractor_name="my-extractor",
            factory=lambda gateway: MyExtractor(gateway),
        )

    # Optional: contribute routes, workflows, hook subscriptions
    def get_routes(self): return ()
    def get_workflows(self): return ()
    def get_hook_subscriptions(self): return ()  # [(hook_name, handler, priority)]

    # Optional: require a commercial license key
    requires_license_key = False
```

### 2. `PluginManifest` + `PluginLifecycle`

For plugins that need the full lifecycle (register → bootstrap → shutdown)
and a `PluginContext` with runtime services.

```python
from kt_plugins import PluginContext, PluginManifest
from kt_plugins.extension_points import ExtensionRegistry

class MyLifecycle:
    async def register(self, registry: ExtensionRegistry) -> None:
        """Phase 1 — declare extensions. No services yet."""
        registry.add_route(my_route_contribution)
        registry.add_workflow(my_hatchet_workflow)

    async def bootstrap(self, ctx: PluginContext) -> None:
        """Phase 2 — subscribe to hooks, open connections."""
        ctx.hook_registry.register(
            "usage.record", self._record_billing, priority=50, plugin_id=ctx.plugin_id
        )

    async def shutdown(self) -> None:
        """Cleanup."""

plugin_manifest = PluginManifest(
    id="my-plugin",
    name="My Plugin",
    version="1.0.0",
    requires_license_key=False,
    lifecycle=MyLifecycle(),
    settings_class=MyPluginSettings,  # optional Pydantic BaseSettings
    dependencies=[],                  # plugin IDs this one depends on
)
```

---

## PluginContext

Handed to `lifecycle.bootstrap()`:

```python
@dataclass
class PluginContext:
    plugin_id: str
    settings: Any                         # resolved plugin settings (or None)
    hook_registry: HookRegistry
    session_factory: async_sessionmaker | None        # graph-db
    write_session_factory: async_sessionmaker | None  # write-db
    model_gateway: Any                     # kt_models.gateway.ModelGateway
    embedding_service: Any                 # kt_models.embeddings.EmbeddingService
    provider_registry: Any                 # kt_providers.registry.ProviderRegistry
```

The API populates all fields; workers populate all fields including `model_gateway`
and `embedding_service`. Non-runtime contexts may leave services as `None`.

---

## Extension Points

### Routes

Mounted under `/api/v1/plugins/<prefix>`.

```python
from fastapi import APIRouter
from kt_plugins import RouteContribution
from kt_rbac.types import Permission

router = APIRouter(tags=["my-plugin"])

@router.get("/status")
async def status():
    return {"ok": True}

RouteContribution(
    router=router,
    prefix="my-plugin",
    auth_required=True,
    require_permission=Permission.SYSTEM_ADMIN_OPS,  # optional — kt_rbac.Permission
)
```

### Hatchet Workflows

```python
from kt_plugins import WorkflowContribution

WorkflowContribution(workflow=my_wf)  # or registry.add_workflow(my_wf)
```

Workers collect plugin workflows and register them alongside core workflows.

### Search Providers

```python
from kt_plugins import SearchProviderContribution

SearchProviderContribution(
    provider_id="my-source",
    factory=lambda: MyProvider(),
    is_available=lambda: bool(os.environ.get("MY_API_KEY")),
)
```

Selected at startup when `settings.default_search_provider == provider_id` or
`== "all"`.

### Entity Extractors

```python
from kt_plugins import EntityExtractorContribution

EntityExtractorContribution(
    extractor_name="my-extractor",
    factory=lambda gateway: MyExtractor(gateway),
)
```

Selected when `settings.entity_extractor == extractor_name`.

### Post-Extraction Hooks

Persist extractor side outputs (e.g. shell candidates):

```python
from kt_plugins import PostExtractionHook

async def _persist(write_session, items, scope): ...

PostExtractionHook(extractor_name="hybrid", output_key="shells", handler=_persist)
```

### Plugin Database

Each plugin may own one schema on either database:

```python
from kt_plugins import PluginDatabase

PluginDatabase(
    plugin_id="my-plugin",
    schema_name="plugin_my",                       # NOT "public"
    alembic_config_path=Path("plugins/my/alembic.ini"),
    target="write",                                 # or "graph"
    schema_env_var="ALEMBIC_SCHEMA",                # optional — enables per-schema migrations
)
```

Migrations run at startup (best-effort for third-party plugins, strict for core).
Core tables may reference plugin tables only via foreign keys that your
plugin creates; the core platform never references plugin tables.

---

## Hook System

Priority-ordered async hooks. Lower priority runs first (default 100).

- **trigger**: fire all handlers, collect results. Failures per handler are
  logged and swallowed — one bad handler cannot abort the chain.
- **filter**: chain handlers, each transforming a value.
- **fire_and_forget**: schedule a trigger on the running loop and return
  immediately — used for hot-path audit hooks that must not block.

### Core Hooks

| Hook | Type | Fired when | Keyword args |
|---|---|---|---|
| `auth.user_created` | trigger | After user registration | `user_id`, `email`, `method` |
| `auth.user_login` | trigger | After successful login | `user_id`, `method`, `ip`, `user_agent` |
| `auth.user_deleted` | trigger | On user deletion | `user_id` |
| `auth.permission_check` | trigger (fire-and-forget) | On every permission check | `user_id`, `permission`, `granted`, `graph_role`, `is_default_graph` |
| `usage.record` | trigger | After LLM usage recorded | `user_id`, `cost_usd`, `prompt_tokens`, `completion_tokens`, `model_id`, `task_type` |
| `usage.pre_workflow` | filter | Before workflow dispatch | `user_id`, `workflow_name`, `estimated_cost` → returns `{"allowed": bool, "reason": str}` |

Subscribe inside `bootstrap()` (or via `get_hook_subscriptions()` on the
legacy ABC):

```python
async def bootstrap(self, ctx: PluginContext) -> None:
    ctx.hook_registry.register(
        "auth.user_login",
        self._audit_login,
        priority=100,
        plugin_id=ctx.plugin_id,
    )
```

---

## Plugin Settings

Plugins declare settings via a Pydantic `BaseSettings` subclass. Convention:

- Env var prefix: `<PLUGIN_NAME>_*` (e.g. `CONCEPT_EXTRACTOR_*`).
- YAML section: `<plugin_name>:` in `config.yaml`.

See `plugins/backend-engine-concept-extractor/src/kt_plugin_be_concept_extractor/settings.py`
for a reference implementation that chains env → .env → YAML → defaults.

---

## License Keys (Commercial Plugins)

Plugins with `requires_license_key=True` must present a valid HMAC-SHA256 key
at startup. Keys are configured either via Settings:

```yaml
plugins:
  license_keys:
    billing: "<payload>.<signature>"
    sso: "<payload>.<signature>"
```

…or via env var (`PLUGIN_LICENSE_KEYS` as JSON). Validation is offline and
runs at startup only — no phone-home. Plugins failing validation are
silently dropped; the service still starts.

Each commercial plugin ships with its own signing key. `kt-plugins` uses a
default signing key for tests and for simple self-hosted commercial plugins;
production-grade issuance tooling is out of tree.

---

## Selection Policies

- **Routes, workflows, post-extraction hooks, DB schemas** — always loaded.
- **Entity extractor** — the contribution whose `extractor_name` matches
  `settings.entity_extractor` is instantiated.
- **Search provider** — all contributions whose `provider_id` matches
  `settings.default_search_provider` (or `"all"`) are registered; other
  providers' factories are never called.

---

## Discovery Order

1. `importlib.metadata` entry-point group `kt.plugins`.
2. Legacy hardcoded target list (in-tree plugins not yet declaring entry
   points). Deduplicated by plugin ID — entry points win.
3. `settings.enabled_plugins` allowlist filters the final set (empty = all).

---

## Core Platform Boundary

Under the AGPL Plugin Exception, the following remain **core platform** and
subject to AGPLv3. Changes to them (rather than extensions *through* this
Plugin API) are derivative works:

- Knowledge graph engine and database models
- LLM system prompts and prompt templates
- Synthesis agents and their tools
- Fact extraction and decomposition pipelines
- Document processing pipeline
- Core user interfaces (`frontend/`, `wiki-frontend/`)
- API service and endpoint logic
- Worker services and workflow definitions
- Authentication backends (`services/api/src/kt_api/auth/`)
- RBAC (`libs/kt-rbac/`)

The Plugin Exception permits extending, but not modifying, these boundaries
through the API documented above.
