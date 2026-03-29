# Knowledge Tree Plugin API

This document defines the **Plugin API** referenced by the AGPL Plugin Exception in the [LICENSE](LICENSE) (Section 7, lines 237-289). Independent modules that communicate **solely** through this documented API may be conveyed under terms of your choice.

## Overview

Knowledge Tree plugins are standard Python packages discovered via `importlib.metadata` entry points. They extend the platform through typed extension points, async hooks, and FastAPI route contribution — without modifying core source code.

## Plugin Package Structure

```
kt-plugin-<name>/
  pyproject.toml              # declares entry point + dependencies
  src/kt_plugin_<name>/
    __init__.py
    manifest.py               # PluginManifest instance
    lifecycle.py              # PluginLifecycle implementation
    routes.py                 # FastAPI router(s) (optional)
    models.py                 # SQLAlchemy models (optional)
    migrations/               # Alembic migrations (optional)
      env.py
      versions/
    settings.py               # Plugin-specific Pydantic settings (optional)
  tests/
```

### Entry Point Declaration

In `pyproject.toml`, declare your plugin in the `kt.plugins` entry point group:

```toml
[project.entry-points."kt.plugins"]
<name> = "kt_plugin_<name>.manifest:plugin_manifest"
```

The entry point must resolve to a `PluginManifest` instance.

## PluginManifest

```python
from kt_plugins.manifest import PluginManifest

plugin_manifest = PluginManifest(
    id="my-plugin",                    # unique identifier
    name="My Plugin",                  # human-readable name
    version="1.0.0",
    description="What this plugin does",
    author="Your Name",
    license="proprietary",             # or "AGPL-3.0"
    requires_license_key=False,        # True for commercial plugins
    lifecycle=MyPluginLifecycle(),      # PluginLifecycle implementation
    settings_class=MyPluginSettings,   # Pydantic BaseSettings subclass (optional)
    migration_path="path/to/migrations", # Alembic versions dir (optional)
    dependencies=["other-plugin"],     # Plugin IDs this depends on (optional)
)
```

## PluginLifecycle Protocol

Plugins implement the `PluginLifecycle` protocol:

```python
from kt_plugins.extension_points import ExtensionRegistry
from kt_plugins.context import PluginContext

class MyPluginLifecycle:
    async def register(self, registry: ExtensionRegistry) -> None:
        """Phase 1: Declare extensions. No runtime services available yet."""
        registry.add_routes(my_router, auth_required=True, prefix="my-plugin")

    async def bootstrap(self, ctx: PluginContext) -> None:
        """Phase 2: Access runtime services, subscribe to hooks."""
        ctx.hook_registry.register("usage.record", my_handler, priority=100)

    async def shutdown(self) -> None:
        """Cleanup on application shutdown."""
        pass
```

### Lifecycle Phases

1. **Discovery** — Plugins found via `importlib.metadata` entry points
2. **Register** — `register()` called; declare routes, providers, hooks, workflows
3. **Bootstrap** — `bootstrap()` called with `PluginContext`; access services, subscribe to hooks
4. **Shutdown** — `shutdown()` called in reverse order on application exit

## PluginContext

Provided during the bootstrap phase:

```python
@dataclass
class PluginContext:
    plugin_id: str                                          # Your plugin's ID
    settings: Any                                           # Your resolved settings (or None)
    hook_registry: HookRegistry                             # Subscribe to hooks
    session_factory: async_sessionmaker[AsyncSession] | None  # Graph-db sessions
    write_session_factory: async_sessionmaker[AsyncSession] | None  # Write-db sessions
```

## Extension Points

### Routes

Contribute FastAPI routers mounted at `/api/v1/plugins/<prefix>/`:

```python
from fastapi import APIRouter

router = APIRouter(tags=["my-plugin"])

@router.get("/status")
async def get_status():
    return {"status": "ok"}

# In register():
registry.add_routes(router, auth_required=True, prefix="my-plugin")
```

### Knowledge Providers

Contribute search providers implementing `KnowledgeProvider` ABC:

```python
registry.add_provider(lambda ctx: MySearchProvider(ctx.settings.api_key))
```

### Auth Backends

Contribute authentication backends (SSO, OIDC, SAML):

```python
registry.add_auth_backend(lambda ctx: create_saml_backend(ctx.settings))
```

### Workflows

Contribute Hatchet workflows:

```python
registry.add_workflow(my_hatchet_workflow)
```

### Custom Types

Register new node or fact types:

```python
registry.add_node_type("custom_type", {"description": "My custom node type"})
registry.add_fact_type("custom_fact", {"description": "My custom fact type"})
```

## Hook System

Hooks enable cross-cutting concerns without modifying core code. Two invocation styles:

### Trigger Hooks (Actions)

Fire all handlers, collect results:

```python
# Subscribe in bootstrap():
ctx.hook_registry.register("usage.record", my_handler, priority=100, plugin_id="my-plugin")

# Handler signature:
async def my_handler(**kwargs) -> Any:
    user_id = kwargs["user_id"]
    cost_usd = kwargs["cost_usd"]
    # ... process usage ...
```

### Filter Hooks

Chain handlers that transform a value:

```python
ctx.hook_registry.register("usage.pre_workflow", my_filter, priority=10)

async def my_filter(value, **kwargs):
    # value is the current result; return transformed value
    if insufficient_credits(kwargs["user_id"]):
        return {"allowed": False, "reason": "No credits"}
    return value
```

### Available Core Hooks

| Hook Name | Type | Fired When | Kwargs |
|---|---|---|---|
| `auth.user_created` | trigger | After user registration | `user_id`, `email` |
| `auth.user_login` | trigger | After successful login | `user_id`, `method` |
| `usage.record` | trigger | After LLM usage recorded | `user_id`, `cost_usd`, `prompt_tokens`, `completion_tokens`, `model_id`, `task_type` |
| `usage.pre_workflow` | filter | Before workflow dispatch | `user_id`, `workflow_name`, `estimated_cost` |

### Priority

Lower priority values run first (default: 100). Use priority < 100 for handlers that must run early (e.g., billing checks), > 100 for those that run late (e.g., audit logging).

## Database Extensions

Plugins may create their own tables using a separate Alembic migration chain.

### Rules

- Each plugin uses its own version table: `alembic_version_<plugin_id>`
- Table names must use `plugin_<id>_` prefix to avoid collisions
- Plugins may reference core tables via foreign keys
- Core tables never reference plugin tables

### Setup

Set `migration_path` in your manifest to point to your Alembic versions directory. Migrations run automatically during application startup.

## Plugin Settings

Plugins declare settings via a Pydantic `BaseSettings` subclass:

```python
from pydantic_settings import BaseSettings

class MyPluginSettings(BaseSettings):
    my_plugin_api_key: str = ""
    my_plugin_timeout: int = 30

    model_config = {"env_prefix": "KT_PLUGIN_MYPLUGIN_"}
```

Convention:
- Environment variables: `KT_PLUGIN_<ID>_*`
- YAML config (in `config.yaml`):

```yaml
plugins:
  my-plugin:
    api_key: "..."
    timeout: 30
```

## License Keys (Commercial Plugins)

Commercial plugins set `requires_license_key=True` in their manifest. The license key is configured in the core settings:

```yaml
plugins:
  license_keys:
    billing: "payload.signature"
    sso: "payload.signature"
```

Or via environment variable: `PLUGIN_LICENSE_KEYS='{"billing": "payload.signature"}'`

License validation uses HMAC-SHA256 and runs at startup only (offline, no phone-home).

## Example: Minimal Provider Plugin

```python
# kt_plugin_jira/manifest.py
from kt_plugins.manifest import PluginManifest

class JiraLifecycle:
    async def register(self, registry):
        registry.add_provider(lambda ctx: JiraProvider(ctx.settings.jira_url))

    async def bootstrap(self, ctx):
        pass

    async def shutdown(self):
        pass

plugin_manifest = PluginManifest(
    id="jira",
    name="Jira Integration",
    version="1.0.0",
    lifecycle=JiraLifecycle(),
)
```

## Example: Minimal Route Plugin

```python
# kt_plugin_analytics/manifest.py
from fastapi import APIRouter
from kt_plugins.manifest import PluginManifest

router = APIRouter(tags=["analytics"])

@router.get("/dashboard")
async def dashboard():
    return {"charts": []}

class AnalyticsLifecycle:
    async def register(self, registry):
        registry.add_routes(router, prefix="analytics")

    async def bootstrap(self, ctx):
        pass

    async def shutdown(self):
        pass

plugin_manifest = PluginManifest(
    id="analytics",
    name="Analytics Dashboard",
    version="1.0.0",
    lifecycle=AnalyticsLifecycle(),
)
```

## Core Platform Boundary

The following components are **core platform** and remain under AGPLv3:

- Knowledge graph engine and database models
- LLM system prompts and prompt templates
- Synthesis agents and their tools
- Fact extraction and decomposition pipelines
- Document processing pipeline
- Core user interfaces (frontend, wiki-frontend)
- API service and endpoint logic
- Worker services and workflow definitions

Plugins that modify these components (rather than extending through the Plugin API) are derivative works subject to AGPLv3.
