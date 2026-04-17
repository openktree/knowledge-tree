# Knowledge Tree — Agent Context

## What This Project Is

Knowledge Tree is a **knowledge integration system** that builds understanding exclusively from raw external data — never from AI model internal knowledge. It constructs and evolves a knowledge graph where every node (concept) is grounded in provenance-tracked facts decomposed from real sources (web search APIs, uploaded files, links).

The system is designed so that over time, frequently queried topics accumulate increasingly rich factual bases. Multiple AI models reason over the same fact base to produce different dimensions of each node, and convergence across models reveals genuine consensus while divergence reveals where biases determine conclusions.

**This is NOT a chatbot or RAG system.** It is a persistent, growing knowledge graph with agents that navigate, expand, and synthesize from it. The primary interaction model is a **document-based research interface** where users ingest sources to grow the graph, then create synthesis documents that weave evidence into analytical narratives.

## Architecture Documents

- **`ARCHITECTURE.md`** — Full requirements, data model, agent architecture, API design, UI requirements. Source of truth for WHAT to build.
- **`STACK.md`** — Technology choices, library selections, testing strategy. Source of truth for HOW to build it.
- **`plan.md`** — Original implementation plan (already implemented). Reference for understanding phase history.
- **`prompt.md`** — Original design vision document. Reference for understanding intent.

## Technology Stack

### Backend (Python — microservices monorepo)
- **Runtime:** Python 3.12+ managed with **uv** (workspace monorepo)
- **API:** FastAPI + uvicorn (async, SSE streaming)
- **ORM:** SQLAlchemy 2.0 (async) + asyncpg
- **Database:** Dual PostgreSQL 16 — graph-db (pgvector, FK constraints) + write-db (normalized, no FKs, deterministic TEXT keys)
- **Migrations:** Alembic — `alembic/` for graph-db, `alembic_write/` for write-db (both owned by kt-db)
- **Orchestration:** Hatchet v1 SDK (durable workflow engine)
- **Agents:** LangGraph (stateful graph-based agent orchestration, used within Hatchet tasks)
- **AI Models:** LiteLLM → OpenRouter (multi-model: Claude, Gemini, GPT, Grok, Llama, GLM)
- **Embeddings:** OpenAI text-embedding-3-large via OpenRouter (3072 dimensions)
- **Knowledge Providers:** Serper (default), Brave Search API (extensible via abstract interface)
- **Auth:** fastapi-users (JWT + Google OAuth) + API tokens
- **Cache:** Redis (general caching)
- **Validation:** Pydantic v2 for all schemas
- **Linting:** ruff (lint + format), pyright (type checking)
- **Testing:** pytest + pytest-asyncio + pytest-xdist (parallel), 556+ tests across 10 packages

### Frontend (TypeScript)
- **Framework:** Next.js 16 (App Router) + React 19 + TypeScript
- **Graph Viz:** Cytoscape.js + react-cytoscapejs + cytoscape-fcose
- **UI:** shadcn/ui + Tailwind CSS v4
- **Real-time:** Server-Sent Events (SSE) via @microsoft/fetch-event-source
- **Package Manager:** pnpm
- **Testing:** Vitest + React Testing Library, 124+ tests

### Infrastructure
- **PostgreSQL 16 — graph-db** (pgvector) — read-optimized graph database with FK constraints, embeddings, pgvector search
- **PostgreSQL 16 — write-db** — write-optimized database, normalized, no FKs, deterministic TEXT keys, no deadlocks
- **Redis 7** — general caching
- **Hatchet** (v0.79.22) — durable workflow orchestration engine (with its own Postgres)
- **Docker Compose** — local dev for infrastructure + production-like deployment for all services

## Project Structure

**uv workspace monorepo** with shared libraries (`libs/`) and deployable services (`services/`). Each package follows `src/kt_<name>/` layout with `tests/` alongside.

**Shared libraries (`libs/`)** — Reusable infrastructure with no agent/LLM logic. Each is a uv workspace package (`src/kt_<name>/` layout). Create new libs only when shared by 2+ packages.
- **kt-config** — Settings, shared enums, custom errors
- **kt-db** — SQLAlchemy models (both DBs), repositories, Alembic migrations (`alembic/` graph-db, `alembic_write/` write-db)
- **kt-models** — AI model gateway (LiteLLM), embeddings
- **kt-providers** — Knowledge providers (Serper, Brave), abstract `KnowledgeProvider` ABC
- **kt-graph** — GraphEngine — node/edge CRUD, search, convergence
- **kt-facts** — Fact decomposition, extraction, dedup
- **kt-hatchet** — Hatchet client singleton, worker state, workflow I/O models. Depends on `kt-graph` so `WorkerState.make_worker_engine()` can wire a `PublicGraphBridge` into every per-workflow engine. The dep direction is one-way: `kt-graph` MUST NOT import `kt-hatchet`.
- **kt-agents-core** — Base agent classes, shared state models
- **kt-qdrant** — Qdrant vector search repositories

**Services (`services/`)** — Deployable processes. Workers own their agents, prompts, and workflow logic. Create new workers only for distinct workflow domains.
- **api** — FastAPI REST + SSE, auth, dependency injection. NO agent code.
- **worker-bottomup** — Bottom-up discovery workflows (scope extraction, node selection)
- **worker-synthesis** — Synthesis agent workflows (synthesizer + super-synthesizer)
- **worker-nodes** — Node creation pipeline (create → dimensions → definition → parent)
- **worker-search** — Standalone search workflow
- **worker-ingest** — File/link ingestion agent + pipeline
- **worker-sync** — Write-db → graph-db incremental sync
- **worker-all** — Dev-mode: all workflows in single process

**Frontend** (`frontend/src/`) — Next.js App Router. Components organized by domain in `components/`, hooks in `hooks/`, typed API client in `lib/api.ts`.

**Other:** `wiki-frontend/` (Astro wiki UI), `config/` (YAML configs)

### Import Names
Pattern: `kt-<name>` → `kt_<name>`, `worker-<name>` → `kt_worker_<name>`, `api` → `kt_api`.

## Key Architectural Concepts

### Dual Database Architecture (graph-db + write-db)
The system uses **two PostgreSQL databases** optimized for different workloads:

- **graph-db** — Read-optimized. Has pgvector, FK constraints, junction tables (EdgeFact, DimensionFact, NodeFact). API reads from graph-db. **Pipelines never write directly to graph-db** — the sync worker is the sole writer.
- **write-db** — Write-optimized. Normalized, NO foreign keys, TEXT primary keys (deterministic). No deadlocks possible. Agents write here during pipelines.

**Deterministic UUIDs** bridge the two databases: `key_to_uuid(write_key)` uses UUID5 to derive the same UUID from a write-db TEXT key, so both databases share identical IDs for every entity. Defined in `kt_db.keys`.

**Write routing rules:**
| Entity | Where agents write | Why |
|---|---|---|
| **Nodes** | Write-db + Qdrant | Qdrant provides immediate vector search; sync worker creates graph-db Node + NodeFact junction rows |
| **Edges** | Write-db ONLY | Eliminates FK-validation deadlocks; sync worker creates graph-db Edge + EdgeFact junction rows |
| **Dimensions** | Write-db ONLY | Same reasoning; sync worker creates graph-db Dimension + DimensionFact junction rows |
| **Definitions** | Write-db ONLY | Stored on WriteNode; sync worker propagates to graph-db |
| **Parents** | Write-db ONLY | Stored as parent_key on WriteNode; sync worker resolves FK |
| **Counters** | Write-db ONLY | Sync worker propagates to graph-db NodeCounter |
| **Convergence** | Write-db ONLY | Sync worker propagates to graph-db ConvergenceReport |
| **Facts** | Write-db + Qdrant | WriteFact (UUID PK), WriteFactSource (denormalized); sync worker creates graph-db Fact + FactSource rows |

**Node cache**: GraphEngine maintains an in-memory `_node_cache` populated by `create_node()`. Subsequent methods (`add_dimension`, `create_edge`, `set_parent`, `link_fact_to_node`) check the cache first for node info (type, concept) needed for write-db key generation, falling back to graph-db for nodes from previous pipeline runs.

**Sync worker** (`worker-sync`): Single-threaded Hatchet worker polls write-db by `updated_at > watermark` per table, upserts into graph-db, and creates junction rows (NodeFact, EdgeFact, DimensionFact) from stored `fact_ids` arrays. Watermarks stored in `sync_watermarks` table.

**Key files:**
- `kt_db/keys.py` — `make_node_key()`, `make_edge_key()`, `make_dimension_key()`, `key_to_uuid()`
- `kt_db/write_models.py` — WriteNode, WriteEdge, WriteDimension, WriteConvergenceReport, WriteNodeCounter, SyncWatermark
- `kt_db/repositories/write_*.py` — Write-db repository classes
- `kt_graph/engine.py` — `GraphEngine` routes writes to write-db, reads from graph-db (with node cache for pipeline-created nodes)
- `services/worker-sync/src/kt_worker_sync/sync_engine.py` — `SyncEngine` incremental sync

### Flat Graph (NOT a tree)
Nodes are flat peers — no parent-child hierarchy. All structure comes from typed, weighted edges (weight = shared fact count). Circular references are valid (A->B->C->A). Edges are created from seed co-occurrence candidates accumulated during fact decomposition — not from embedding similarity.

### Research Flow
The primary research interface has two modes:
1. **Ingestion** — Bottom-up discovery: ingest sources, extract entities/facts, create seeds, promote to nodes
2. **Synthesis** — Document synthesis: a synthesizer agent navigates the graph with an exploration budget and produces a standalone research document. A super-synthesizer orchestrates multiple synthesizer agents for comprehensive coverage.

### Exploration Budget
The synthesizer agent uses an **exploration budget** (number of nodes it can visit). This controls investigation depth without limiting free operations like search.

### Microservices Architecture
The backend is a **uv workspace monorepo** split into shared libraries (`libs/`) and deployable services (`services/`). Key design decisions:

1. **uv workspaces** — Single lockfile, each package is independently installable
2. **Each worker owns its agents + prompts** — No agent code in shared libs, only base classes
3. **Worker-to-worker communication via Hatchet only** — Workers never import each other directly; they dispatch workflows by name
4. **API has NO agent code** — API dispatches Hatchet workflows and reads results
5. **Migrations in one place** — `kt-db` owns all Alembic migrations
6. **Dev mode via worker-all** — Registers all workflows on single process for local dev

### Hatchet Workflow Architecture
All heavy processing runs as **durable Hatchet workflows** — not in-process or via simple background tasks.

**Main workflow flows:**

**Ingestion (bottom-up discovery):**
1. User submits sources (files/links) via the research UI
2. `ingest_build_wf` extracts facts, entities, creates seeds
3. Seeds accumulate facts and are promoted to nodes via `node_pipeline_wf`
4. `node_pipeline_wf` creates node -> dimensions -> definition -> parent

**Synthesis:**
1. User creates a synthesis via POST `/api/v1/syntheses`
2. `synthesizer_wf` runs the SynthesizerAgent (navigates graph, produces document)
3. Document processing pipeline splits, embeds, and links sentences to facts/nodes
4. User creates a super-synthesis via POST `/api/v1/super-syntheses`
5. `super_synthesizer_wf` plans scopes, dispatches N `synthesizer_wf` in parallel, combines results

**Key workflows:**
- `synthesizer_wf` — Synthesis document creation — `kt_worker_synthesis`
- `super_synthesizer_wf` — Multi-scope super-synthesis (reconnaissance → dispatch → combine) — `kt_worker_synthesis`
- `node_pipeline_wf` — Node creation DAG (create_node -> dimensions -> definition -> parent) — `kt_worker_nodes`
- `bottom_up_wf` — Bottom-up scope exploration — `kt_worker_bottomup`
- `search_wf` — Standalone search — `kt_worker_search`
- `ingest_build_wf` — Source ingestion pipeline — `kt_worker_ingest`

### Agent Architecture
Agents (LangGraph-based) are used **within** Hatchet tasks for LLM reasoning:
1. **Synthesizer Agent** (`kt_worker_synthesis.agents.synthesizer_agent`) — Navigates the graph with an exploration budget, produces a synthesis document. 8 navigation tools + finish_synthesis.
2. **SuperSynthesizer Agent** (`kt_worker_synthesis.agents.super_synthesizer_agent`) — Reads sub-synthesis documents and produces a meta-synthesis.
3. **Ingest Agent** (`kt_worker_ingest.agents.ingest_agent`) — File/link ingestion into the knowledge graph.
4. **Bottom-up Scope** (`kt_worker_bottomup.bottom_up.scope`) — Fact gathering and node extraction from search results.

### Authentication
- **fastapi-users** with JWT + refresh tokens
- **Google OAuth** support
- **API tokens** for programmatic access (MCP, etc.)
- `require_auth` dependency on all routes except auth endpoints
- Tests bypass auth via `SKIP_AUTH=true` env var

### Node Types
- `concept` — Abstract topic, idea, technique, phenomenon
- `entity` — Subject capable of intent (person, organization)
- `event` — Temporal occurrence (historical, scientific, ongoing)
- `synthesis` — Composite document synthesizing multiple nodes (has sentences, fact links)
- `supersynthesis` — Meta-synthesis combining multiple synthesis documents

### Edge Types (2 active types)
All edges are undirected with canonical UUID ordering enforced (smaller UUID always stored as source). One edge per type per node pair is enforced via DB unique constraint.
- `related` — Connects nodes of the same type. Weight = shared fact count (positive float). Created from seed co-occurrence candidates with LLM-generated justification.
- `cross_type` — Connects nodes of different types (e.g., entity<->event, concept<->entity). Same weight semantics.

Weight is a **fact count** (always positive) — higher values mean stronger evidence. Edges are created from `write_edge_candidates` accumulated during seed extraction. The LLM generates a justification only (no weight decision). Relationship type is determined by node types: same type = `related`, different = `cross_type`.

Parent-child structure uses the `parent_id` FK on the Node model, not edges.

### Facts Layer
Facts are independent of nodes. A fact can be linked to many nodes. Facts accumulate sources over time (deduplication by embedding similarity). Provenance chain: Node -> Fact -> RawSource.

### Data Model (DB Tables)
See `kt_db/models.py` for graph-db tables and `kt_db/write_models.py` for write-db tables.

### Real-Time Streaming
Progress is streamed via **Server-Sent Events (SSE)**:
- Hatchet tasks emit events via `ctx.aio_put_stream(json.dumps({"type": ..., ...}))`
- Synthesis workflows emit progress events (agent_started, synthesis_completed)

---

## CRITICAL: Package Manager Rules

### Backend: ONLY use `uv`

**NEVER use `pip`, `pip install`, `poetry`, or manually edit `pyproject.toml` dependencies.**

```bash
# CORRECT — add a dependency to a specific package
cd libs/kt-config && uv add <package>
cd services/api && uv add <package>
cd libs/kt-db && uv add --dev <package>

# CORRECT — install/sync all packages
uv sync --all-packages

# CORRECT — run a command for a specific package
uv run --project libs/kt-config pytest -x -v
uv run --project services/api uvicorn kt_api.main:app --reload

# WRONG — never do these
pip install <package>              # WRONG
pip install -r requirements.txt    # WRONG
python -m pytest                   # WRONG (use uv run)
cd backend && uv run ...           # WRONG (backend/ no longer exists)
```

### Frontend: ONLY use `pnpm`

**NEVER use `npm`, `yarn`, `bun`, or manually edit `package.json` dependencies.**

```bash
# CORRECT — add a dependency
cd frontend && pnpm add <package>
cd frontend && pnpm add -D <package>

# CORRECT — install
cd frontend && pnpm install

# CORRECT — run scripts
cd frontend && pnpm dev
cd frontend && pnpm test

# CORRECT — add shadcn components
cd frontend && pnpm dlx shadcn@latest add <component>

# WRONG — never do these
npm install <package>    # WRONG
npm run dev              # WRONG
yarn add <package>       # WRONG
npx shadcn ...           # WRONG (use pnpm dlx)
```

---

## CRITICAL: Testing After Every Change

**Every code change MUST be verified before completion.** No exceptions.

### Backend verification:
```bash
docker compose up -d postgres postgres-write redis   # Ensure infrastructure

# Run tests for the specific package you changed:
uv run --project libs/kt-config pytest libs/kt-config/tests/ -x -v
uv run --project services/api pytest services/api/tests/ -x -v

# Or run all tests:
just test-all
```

### Frontend verification:
```bash
cd frontend && pnpm lint && pnpm type-check && pnpm test   # All three must pass
```

### When to run tests:
- After modifying ANY source file
- After adding/removing dependencies
- After modifying configuration
- Before declaring any task complete

---

## Existing Code Patterns

Follow these established patterns when making changes. Do NOT invent new patterns.

### Repository Pattern (SQLAlchemy async)
All database access goes through repository classes in `kt_db.repositories`.

```python
# Example from kt_db/repositories/nodes.py
from kt_db.models import Node

class NodeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, concept: str, embedding: list[float] | None = None, ...) -> Node:
        node = Node(id=uuid.uuid4(), concept=concept, embedding=embedding, ...)
        self._session.add(node)
        await self._session.flush()
        return node

    async def get_by_id(self, node_id: uuid.UUID) -> Node | None:
        result = await self._session.execute(select(Node).where(Node.id == node_id))
        return result.scalar_one_or_none()
```

### Upserts via `pg_insert().on_conflict_do_nothing/update()`
No `begin_nested()` — use PostgreSQL upsert patterns directly.

### Advisory Locks for Concept Creation
`pg_advisory_xact_lock` serializes concurrent creation of the same concept.

### Hatchet Workflow Pattern
Workflows are defined as decorated functions registered with the Hatchet SDK.

```python
from kt_hatchet.client import get_hatchet

hatchet = get_hatchet()

@hatchet.workflow(name="node_pipeline_wf", timeout=str(timedelta(minutes=30)))
class NodePipelineWorkflow:
    @hatchet.task(name="create_node")
    async def create_node(self, ctx: DurableContext) -> dict: ...

    @hatchet.task(name="dimensions", parents=["create_node"])
    async def dimensions(self, ctx: DurableContext) -> dict: ...
```

Tasks emit progress via `ctx.aio_put_stream(json.dumps({"type": ..., ...}))`.

### Abstract Provider Interface
New providers implement the abstract base class in `kt_providers.base`.

```python
from kt_providers.base import KnowledgeProvider

class KnowledgeProvider(ABC):
    @property
    @abstractmethod
    def provider_id(self) -> str: ...

    @abstractmethod
    async def search(self, query: str, max_results: int = 10) -> list[RawSearchResult]: ...
```

### Pydantic Settings for Configuration
All config lives in `kt_config.settings` using `pydantic-settings`. Extensive model/thinking-level overrides.

```python
from kt_config.settings import Settings, get_settings

class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://kt:localdev@localhost:5432/knowledge_tree"
    default_model: str = "openrouter/x-ai/grok-4.1-fast"
    # Per-agent model overrides
    decomposition_model: str = "openrouter/google/gemini-3.1-flash-lite-preview"
    orchestrator_model: str = "openrouter/z-ai/glm-5"
    # Per-role thinking levels
    decomposition_thinking_level: str = "low"
```

### FastAPI Router Pattern
API routes are organized per-resource in `kt_api/` with auth protection via central router.

```python
# kt_api/router.py — all routes require auth except auth endpoints
from kt_api.auth.tokens import require_auth

_auth_dep = [Depends(require_auth)]
api_router = APIRouter()
api_router.include_router(auth_router)  # public
api_router.include_router(conversations_router, dependencies=_auth_dep)
api_router.include_router(nodes_router, dependencies=_auth_dep)
```

### Test Fixtures (xdist-safe)
Tests use per-worker PostgreSQL schemas for parallel isolation. Auth bypassed via `SKIP_AUTH=true`. Each package with integration tests has its own `conftest.py`.

### Frontend: Typed API Client
All backend calls go through the typed client in `lib/api.ts`.

```typescript
export const api = {
  getNode: (id: string) => request<NodeResponse>(`/nodes/${id}`),
  submitQuery: (data: QueryRequest) => request<QueryResponse>("/query", { method: "POST", body: JSON.stringify(data) }),
};
```

### Frontend: Custom Hooks Pattern
Complex data fetching uses dedicated hooks in `hooks/`.

```typescript
export function useConversation(conversationId: string): UseConversationResult { ... }
export function usePipelineProgress(messageId: string, isActive: boolean): PipelineProgress { ... }
```

### Frontend: Named Exports for Components
All custom components use **named exports** (not default exports), except page components and dynamically imported components.

---

## CRITICAL: Fail-Fast Principle

**Errors that affect data quality or system correctness MUST propagate — never swallow them.**

This system builds a knowledge graph where bad data (garbage seeds, missing shell classification, silent migration failures) compounds over time and is expensive to clean up. A failed Hatchet task is cheap; corrupted graph state is not.

**Rules:**
1. **Pipeline steps that affect output quality MUST fail the task on error.** If shell classification, entity extraction, fact decomposition, or any step that gates data quality fails, let the exception propagate. The Hatchet task fails, the operator sees it, and the system gets fixed.
2. **Never catch-and-warn for errors that silently degrade results.** A `try/except` that logs a warning and continues is only acceptable when the skipped work is truly optional (e.g. Qdrant collection creation, telemetry). If skipping the step means bad data enters the graph, it is NOT optional.
3. **Migrations must fail loudly.** If a plugin schema doesn't exist, the plugin's writes will silently fail. Startup migrations that succeed in Alembic but roll back silently (e.g. missing `commit()`) are the worst kind of bug — they look green but produce a broken runtime.
4. **Validate at boundaries, fail at boundaries.** When data crosses a trust boundary (external API → pipeline, pipeline → DB), validate and reject early. Don't pass garbage downstream hoping a later stage will filter it.

**Anti-pattern (real incident):** Plugin migration transaction rolled back silently → shell_candidates table didn't exist → PostExtractionHook insert failed → exception caught as "non-fatal" → shell classifier results discarded → all candidates kept as seeds → graph flooded with garbage ("volume", "pages", "authors", "article"). Three silent failures compounded into a data quality disaster.

---

## Common Mistakes to Avoid

1. **Wrong package manager** — Using `npm` instead of `pnpm`, or `pip` instead of `uv`. Always check which directory you're in.
2. **Manually editing dependency files** — Never edit `package.json` or `pyproject.toml` dependency sections directly. Use `pnpm add` / `uv add`.
3. **Skipping tests** — Every change requires running the test suite. No exceptions.
4. **Default imports for components** — Tab components, utility components use named exports. Only pages and dynamic imports use default exports.
5. **Sync database operations** — All DB access must be async. Use `AsyncSession`, `await`, `async def`.
6. **Hardcoded API keys** — Never hardcode keys. Use `.env` + Pydantic Settings.
7. **Direct DB queries outside repositories** — All SQL goes through repository classes in `kt_db.repositories`.
8. **Missing type annotations** — All Python functions need return types. All TypeScript props need interfaces.
9. **Using `begin_nested()` for upserts** — Use `pg_insert().on_conflict_do_nothing/update()` instead. `begin_nested()` IS appropriate for error isolation in batch loops (e.g. auto_build savepoints) where one item's failure should not poison the whole transaction.
10. **Background tasks without Hatchet** — Heavy processing must go through Hatchet workflows, not `BackgroundTasks`.
11. **Cross-worker direct imports** — Workers communicate via Hatchet workflow dispatch only. Use lazy imports (inside functions) when a service needs another service's types for dispatch.
12. **Agent code in shared libs** — Only base classes (`kt_agents_core`) go in libs. Agent implementations belong in their worker service.
13. **Referencing old `backend/` paths** — The monolithic `backend/` directory no longer exists. All code is in `libs/` and `services/`.

---

## CRITICAL: Adding New Code — Where Things Go

The microservices architecture has strict boundaries. Follow these rules when adding ANY new code.

### Decision Tree: Lib vs Service vs Existing Package

**Before creating anything new, ask:**

1. **Is this shared infrastructure with no agent/LLM logic?** → Add to an existing lib or create a new lib in `libs/`
2. **Is this agent code, prompts, or workflow logic?** → Add to the owning worker service in `services/`
3. **Is this a new API endpoint?** → Add to `services/api/src/kt_api/`
4. **Is this a new Hatchet workflow?** → Create a new worker service in `services/` OR add to an existing worker
5. **Does it fit in an existing package?** → **Put it there.** Don't create a new package unless the code clearly doesn't belong anywhere.

### Adding to an Existing Package (preferred)

Most changes belong in an existing package. Use this table:

| You're adding... | Put it in... |
|---|---|
| New graph-db model/table | `libs/kt-db/src/kt_db/models.py` + migration in `alembic/versions/` |
| New write-db model/table | `libs/kt-db/src/kt_db/write_models.py` + migration in `alembic_write/versions/` |
| New repository (graph-db) | `libs/kt-db/src/kt_db/repositories/` |
| New repository (write-db) | `libs/kt-db/src/kt_db/repositories/write_*.py` |
| New Pydantic type/enum | `libs/kt-config/src/kt_config/types.py` |
| New error class | `libs/kt-config/src/kt_config/errors.py` |
| New settings field | `libs/kt-config/src/kt_config/settings.py` |
| New search provider | `libs/kt-providers/src/kt_providers/` (implement `KnowledgeProvider` ABC) |
| New graph algorithm | `libs/kt-graph/src/kt_graph/` |
| New fact extraction strategy | `libs/kt-facts/src/kt_facts/` |
| New Hatchet input/output model | `libs/kt-hatchet/src/kt_hatchet/models.py` |
| New API endpoint | `services/api/src/kt_api/` (new file + register in `router.py`) |
| New synthesis agent/tool/prompt | `services/worker-synthesis/src/kt_worker_synthesis/` |
| New node pipeline stage | `services/worker-nodes/src/kt_worker_nodes/pipelines/` |
| New bottom-up tool | `services/worker-bottomup/src/kt_worker_bottomup/` |
| New ingest pipeline stage | `services/worker-ingest/src/kt_worker_ingest/ingest/` |

### Creating a New Shared Library (`libs/`)

Create a new lib ONLY when you have **shared, reusable infrastructure** that has NO agent/LLM logic, is needed by 2+ packages, and doesn't fit in any existing lib. Copy structure and `pyproject.toml` from an existing lib (e.g., `libs/kt-qdrant`), then run `uv sync --all-packages`. Add tests, update this CLAUDE.md.

### Creating a New Worker Service (`services/`)

Create a new worker ONLY when you have a **distinct domain of Hatchet workflows** that doesn't belong in any existing worker. Follow this structure:

```
services/worker-<name>/
├── pyproject.toml
├── Dockerfile
├── src/kt_worker_<name>/
│   ├── __init__.py
│   ├── workflows/          # Hatchet workflow definitions
│   │   └── <name>.py
│   ├── agents/             # LangGraph agents (if needed)
│   │   └── tools/          # Agent tools
│   └── prompts/            # LLM prompt templates (if needed)
└── tests/
    └── ...
```

Copy `pyproject.toml` and `Dockerfile` from an existing worker (e.g., `services/worker-search`). Then: register workflows in `worker-all/__main__.py`, add Docker Compose service, add `just` commands, run `uv sync --all-packages`. Add tests, update this CLAUDE.md.

### Boundary Rules (NEVER violate these)

| Rule | Why |
|---|---|
| **Workers NEVER import each other at module level** | Would create circular deps and break independent deployment. Use lazy imports inside functions only when dispatching Hatchet workflows. |
| **Libs NEVER import from services** | Libs are shared foundations — they can't depend on service-specific code. |
| **Agent/LLM logic NEVER goes in libs** | Only base classes (`kt_agents_core`) go in libs. All agent implementations, prompts, and tools belong in their worker service. |
| **API NEVER contains agent logic** | API dispatches Hatchet workflows and reads results. It never runs agents directly. |
| **All worker↔worker communication goes through Hatchet** | Workers dispatch workflows by name string, never by importing and calling another worker's code. |
| **New DB models/migrations ONLY in kt-db** | Single source of schema truth. No other package creates tables. |
| **New settings ONLY in kt-config** | Single Settings class, single `.env` file. |
| **Every new package needs tests** | No package ships without a `tests/` directory and at least basic import/unit tests. |

### Creating Alembic Migrations

**NEVER write migration files by hand.** Always use the Alembic CLI to generate the migration scaffold, then edit the generated file to add the actual operations.

```bash
# Graph-db migration (from libs/kt-db/)
cd libs/kt-db
uv run alembic revision -m "description of change"
# Edit the generated file in alembic/versions/ to add upgrade()/downgrade() logic

# Write-db migration (from libs/kt-db/)
cd libs/kt-db
uv run alembic -c alembic_write.ini revision -m "description of change"
# Edit the generated file in alembic_write/versions/ to add upgrade()/downgrade() logic
```

This ensures revision IDs are **randomly generated 12-character hex strings** (Alembic's default), and `down_revision` is automatically set to the current head. Hand-written IDs (e.g. `zzai`, `abc123`) cause collisions when branches diverge.

After creating or editing migrations, always verify a single head:
```bash
uv run alembic heads          # Should show exactly ONE head
uv run alembic -c alembic_write.ini heads  # Same for write-db
```

---

### Starting infrastructure
```bash
just setup                           # Start infra + generate Hatchet token
# OR manually:
docker compose up -d postgres postgres-write redis hatchet
just hatchet-token                   # Generate Hatchet API token into .env
```

### Running the full stack
```bash
# Terminal 1 — API
just api-dev                         # FastAPI on port 8000

# Terminal 2 — Database migrations (first time only)
just migrate                         # Alembic upgrade head (from libs/kt-db)

# Terminal 3 — Hatchet workers
just worker                          # All-in-one worker (dev mode)
# OR individual workers:
just worker-bottomup                 # Bottom-up discovery only
just worker-synthesis                # Synthesis agent only
just worker-search                   # Search only
just worker-nodes                    # Node pipeline only
just worker-ingest                   # Ingest only
just worker-sync                     # Write-db → graph-db sync only

# Terminal 4 — frontend
cd frontend && pnpm dev
```

### After making changes
```bash
# Backend changes — run tests for the changed package
uv run --project libs/kt-facts pytest libs/kt-facts/tests/ -x -v
uv run --project services/api pytest services/api/tests/ -x -v

# All backend tests
just test-all

# Frontend changes
cd frontend && pnpm lint && pnpm type-check && pnpm test
```

### Docker deployment (all services)
```bash
just up-all                          # Start everything via Docker Compose
```

### Useful just commands
```bash
just setup          # Start infra + Hatchet token
just up             # Start infrastructure only
just up-all         # Start everything (infra + API + workers)
just down           # Stop all services
just clean          # Full reset (delete volumes, re-setup, re-migrate)
just worker         # Start all Hatchet workers (dev mode)
just migrate        # Run database migrations
just test-libs      # Run all lib tests
just test-api       # Run API tests
just test-workers   # Run worker tests
just test-all       # Run all backend tests
just test-frontend  # Run frontend tests
```

## Environment

- API keys are in `.env` at the project root (BRAVE_KEY, SERPER_KEY, OPENROUTER_API_KEY, OPENAI_API_KEY)
- Hatchet token: `HATCHET_CLIENT_TOKEN` (generated via `just hatchet-token`)
- OAuth: `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`
- Auth: `JWT_SECRET_KEY` (change in production)
- Docker Compose provides graph-db PostgreSQL (pgvector/pgvector:pg16), write-db PostgreSQL, Redis (redis:7-alpine), Hatchet (hatchet-lite + its own Postgres)
- `DATABASE_URL` — graph-db connection (default: `postgresql+asyncpg://kt:localdev@localhost:5432/knowledge_tree`)
- `WRITE_DATABASE_URL` — write-db connection (default: `postgresql+asyncpg://kt:localdev@localhost:5433/knowledge_tree_write`)
- All services read config via `kt_config.settings.Settings` which loads from `.env`
- Never hardcode API keys or connection strings
- Hatchet UI available at http://localhost:8080 for workflow monitoring

## Constructor Signatures (for reference)
- `ModelGateway(api_key=None)` — reads from Settings internally
- `EmbeddingService(model=None, api_key=None)` — reads from Settings
- `GraphEngine(session, write_session=None)` — takes graph-db AsyncSession + optional write-db session
- `SyncEngine(write_session_factory, graph_session_factory)` — takes both session factories
- `ProviderRegistry()` — no args

## Git Practices

**Repository:** https://github.com/openktree/knowledge-tree/

### Branch & PR Workflow

Every change — no matter how small — **must be made on a new branch with a pull request**. A change is only considered successful when:

1. **Create a new branch** from `main` with a descriptive name (e.g., `feat/add-search-provider`, `fix/node-cache-invalidation`)
2. **Make commits** on that branch (tests passing at each commit)
3. **Push the branch** and **create a pull request** via the GitHub CLI (`gh pr create`)
4. **Ensure all CI pipelines pass** — use `gh pr checks <pr-number> --watch` or `gh run watch` to monitor pipeline status. Do NOT consider the task complete until all checks are green.
5. If pipelines fail, **fix the issues**, push again, and re-verify checks pass.

```bash
# Example workflow
git checkout -b feat/my-change main
# ... make changes, run tests locally ...
git add -A && git commit -m "feat(scope): description"
git push -u origin feat/my-change
gh pr create --fill
gh pr checks <pr-number> --watch   # Wait for all CI checks to pass
```

**A task is NOT complete until the PR exists and all CI pipelines are green.**

### General Rules

- Commit after each completed task (tests passing)
- Do not commit `.env` or API keys
- Use **Conventional Commits** for all commit messages: https://www.conventionalcommits.org/

### Conventional Commit Format

```
<type>(<scope>): <description>

[optional body]

[optional footer(s)]
```

### Types
- `feat` — New feature
- `fix` — Bug fix
- `docs` — Documentation only
- `style` — Formatting, whitespace (no logic change)
- `refactor` — Code change that neither fixes a bug nor adds a feature
- `perf` — Performance improvement
- `test` — Adding or updating tests
- `build` — Build system or dependency changes
- `ci` — CI/CD configuration
- `chore` — Other changes that don't modify src or test files

### Scopes
Use the package or area affected: `kt-config`, `kt-db`, `kt-models`, `kt-providers`, `kt-graph`, `kt-facts`, `kt-hatchet`, `kt-agents-core`, `api`, `worker-bottomup`, `worker-synthesis`, `worker-nodes`, `worker-ingest`, `frontend`, `docker`, etc. Omit scope for cross-cutting changes.

### Examples
```
feat(worker-synthesis): add synthesizer agent
feat(api): add Google OAuth support
feat(worker-ingest): file upload and decomposition pipeline
fix(kt-api): handle missing node gracefully in GET /nodes/:id
refactor(kt-db): extract common query builder for repositories
test(frontend): add unit tests for cost-estimator
build: update uv workspace dependencies
```

### Breaking Changes
Append `!` after the type/scope for breaking changes:
```
feat(api)!: rename /query endpoint to /conversations
```
