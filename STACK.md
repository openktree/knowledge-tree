# Knowledge Tree — Technology Stack & Project Structure

## Table of Contents

1. [Stack Overview](#1-stack-overview)
2. [Backend — Python](#2-backend--python)
3. [Frontend — Next.js](#3-frontend--nextjs)
4. [Database & Storage](#4-database--storage)
5. [External Services](#5-external-services)
6. [Project Structure](#6-project-structure)
7. [Testing Strategy](#7-testing-strategy)
8. [Development Workflow](#8-development-workflow)
9. [Configuration Management](#9-configuration-management)
10. [Deployment](#10-deployment)

---

## 1. Stack Overview

```
+-----------------------------------------------------------------------+
|  Frontend          Next.js 16 (App Router) + React 19 + TypeScript    |
|  Graph Viz         Cytoscape.js + react-cytoscapejs + fcose           |
|  UI Components     shadcn/ui + Tailwind CSS v4                        |
|  Real-time         Server-Sent Events (SSE)                           |
+-----------------------------------------------------------------------+
|  API Layer         FastAPI + uvicorn + SSE streaming                   |
|  MCP Server        FastMCP + OAuth 2.1 (services/mcp)                 |
|  Auth              fastapi-users (JWT + Google OAuth) + API tokens     |
|  Orchestration     Hatchet v1 SDK (durable workflows)                 |
|  Agents            LangGraph (stateful agent orchestration)            |
|  AI Gateway        LiteLLM -> OpenRouter (multi-model)                |
|  Embeddings        OpenAI text-embedding-3-large (3072d) via LiteLLM  |
+-----------------------------------------------------------------------+
|  Database          Dual PostgreSQL 16 (graph-db + write-db)           |
|  Vector Search     Qdrant + pgvector (graph-db)                       |
|  Connection Pool   PgBouncer (read + write)                           |
|  Cache             Redis 7                                            |
|  Workflow Engine   Hatchet (separate Postgres)                        |
|  Migrations        Alembic (graph-db + write-db)                      |
+-----------------------------------------------------------------------+
|  Package Mgmt      uv (Python) / pnpm (Node)                         |
|  Dev Commands      justfile (just runner)                             |
|  Containerization  Docker + docker-compose + Helm/K3D                 |
|  Monitoring        Hatchet UI (http://localhost:8080)                  |
+-----------------------------------------------------------------------+
```

---

## 2. Backend — Python

### 2.1 Runtime & Package Management

| Tool | Version | Purpose |
|------|---------|---------|
| **Python** | 3.12+ | Runtime. 3.12 for performance improvements and better typing. |
| **uv** | latest | Package management, virtual environments, lockfile. Replaces pip/poetry/pipenv. |

### 2.2 Core Libraries

| Library | Purpose | Why this one |
|---------|---------|-------------|
| **FastAPI** | HTTP API + SSE streaming | Async-native, automatic OpenAPI docs, Pydantic integration. |
| **uvicorn** | ASGI server | Production-grade async server for FastAPI. |
| **Pydantic v2** | Data validation & serialization | Type-safe schemas for all data models. Rust-powered v2 for speed. |
| **SQLAlchemy 2.0** | ORM + query builder | Async support, mapped classes, mature ecosystem. |
| **asyncpg** | PostgreSQL async driver | Fastest Python PostgreSQL driver. |
| **pgvector-python** | pgvector integration | SQLAlchemy + asyncpg types for vector columns and similarity search. |
| **Alembic** | Database migrations | Standard for SQLAlchemy. Autogenerate from model changes. |
| **Redis / redis-py** | Ontology cache | Async Redis client for caching ontology lookups. |
| **httpx** | Async HTTP client | For calling search APIs, OpenRouter, and other external services. |
| **sse-starlette** | Server-Sent Events | SSE endpoint support for real-time progress streaming. |
| **structlog** | Structured logging | JSON-formatted logs, context binding. |
| **python-multipart** | Form data parsing | File upload support for ingestion. |
| **pymupdf** | PDF processing | PDF content extraction for file ingestion. |
| **trafilatura** | Web content extraction | Full-text fetch from URLs. |

### 2.3 AI & Agent Libraries

| Library | Purpose | Why this one |
|---------|---------|-------------|
| **LangGraph** | Agent orchestration | Stateful graph-based agent execution. Explicit control flow, typed state, conditional branching. Used within Hatchet tasks for LLM reasoning loops. |
| **LiteLLM** | Multi-model AI gateway | Unified API for 100+ models via OpenRouter. Built-in cost tracking, retries, fallbacks. |
| **tiktoken** | Token counting | Count tokens before sending to models. Enforce content size limits. |
| **langchain-openai** | OpenAI integration | Model adapters for LangGraph tool-calling agents. |
| **langsmith** | Observability | Tracing and debugging for LangGraph agent runs. |

### 2.4 Orchestration — Hatchet

| Library | Purpose | Why this one |
|---------|---------|-------------|
| **hatchet-sdk** | Durable workflow engine | Replaces arq/Redis Streams. Provides durable task execution, DAG workflows, fan-out/fan-in, progress streaming, workflow monitoring UI. |

**Why Hatchet over alternatives:**
- **vs arq/Celery:** Hatchet provides durable execution with automatic retries, DAG-based task dependencies, and a built-in monitoring UI. Simple job queues can't express the bottom_up_wf -> bottom_up_scope_wf -> node_pipeline_wf hierarchy.
- **vs Temporal:** Hatchet is simpler to self-host (single Docker container), has a cleaner Python SDK, and the lite image is perfect for local dev.
- **vs in-process:** Exploration workflows run for minutes and involve many LLM calls. They must survive process restarts and be observable.

**Workflow architecture:**
```
bottom_up_wf (top-level research)
  |-- scope planning (LLM)
  |-- bottom_up_scope_wf (fan-out, one per scope)
  |     |-- fact gathering + decomposition
  |     |-- node_pipeline_wf (fan-out, one per node)
  |           |-- create_node
  |           |-- dimensions (parallel multi-model)
  |           |-- definition
  |           |-- parent selection
  |     |-- edge resolution

synthesizer_wf (synthesis document creation)
  |-- SynthesizerAgent navigates graph (8 tools)
  |-- finish_synthesis → document

super_synthesizer_wf (multi-scope meta-synthesis)
  |-- plan N scopes
  |-- dispatch N synthesizer_wf in parallel
  |-- SuperSynthesizerAgent combines results

ingest_build_wf (source ingestion)
  |-- decompose sources → facts + seeds
  |-- IngestAgent builds nodes from fact pool

sync_wf (write-db → graph-db propagation)
  |-- watermark-based incremental sync
```

### 2.5 Authentication

| Library | Purpose | Why this one |
|---------|---------|-------------|
| **fastapi-users** | User management | JWT auth, registration, password hashing, OAuth integration. Battle-tested with SQLAlchemy async. |
| **httpx-oauth** | OAuth provider | Google OAuth support for social login. |

Auth architecture:
- JWT access tokens (30min) + refresh tokens (30 days)
- Google OAuth via redirect flow
- API tokens (long-lived, hashed) for programmatic access
- `require_auth` dependency on all routes; `SKIP_AUTH=true` in tests
- User model extends `SQLAlchemyBaseUserTableUUID`

### 2.6 Agent Implementation Pattern

Agents use LangGraph within Hatchet tasks. The BaseAgent template-method class provides shared wiring:

```python
class BaseAgent:
    """Template-method pattern — all agents share the same LangGraph wiring."""

    def _build_graph(self) -> StateGraph:
        graph = StateGraph(self.state_class)
        graph.add_node("agent", self._agent_step)
        graph.add_node("tools", self._tool_node)
        graph.set_entry_point("agent")
        graph.add_conditional_edges("agent", self._should_continue, {
            "tools": "tools",
            END: END,
        })
        graph.add_edge("tools", "agent")
        return graph.compile()
```

The **bottom_up_wf** plans scopes and delegates to **bottom_up_scope_wf** instances. Each scope runs in isolation, gathers facts, builds nodes via `node_pipeline_wf`, and returns results. The **SynthesizerAgent** navigates the resulting graph to produce synthesis documents.

---

## 3. Frontend — Next.js

### 3.1 Framework & Build

| Tool | Version | Purpose |
|------|---------|---------|
| **Next.js** | 16+ (App Router) | React framework. SSR for initial load, client-side for interactive graph. |
| **React** | 19+ | UI library. |
| **TypeScript** | 5.x | Type safety across the frontend. |
| **pnpm** | latest | Package manager. Fast, disk-efficient. |

### 3.2 UI Libraries

| Library | Purpose | Why this one |
|---------|---------|-------------|
| **Tailwind CSS v4** | Utility-first styling | Fast iteration, consistent design. |
| **shadcn/ui** | Component library | Accessible, unstyled primitives. Copy-paste — you own the code. |
| **Cytoscape.js** | Graph visualization engine | Purpose-built for network graph exploration. |
| **react-cytoscapejs** | React wrapper for Cytoscape | Declarative Cytoscape in React components. |
| **cytoscape-fcose** | Force-directed layout | Fast compound spring embedder layout for graph viz. |
| **lucide-react** | Icons | Consistent icon set used throughout the UI. |
| **react-markdown** | Markdown rendering | Renders synthesized answers with formatting. |

### 3.3 Why Cytoscape.js

The core UI is **navigating a knowledge graph** — a network with cycles, varying node sizes, typed edges, and potentially thousands of nodes.

| | Cytoscape.js | React Flow | D3.js | Sigma.js v3 |
|---|-------------|-----------|-------|-------------|
| **Designed for** | Network/graph analysis | Flow editors, DAGs | General viz | Large networks |
| **Circular graphs** | Native | Awkward | Manual | Native |
| **Force-directed layout** | Built-in (multiple) | Needs external lib | Manual | Built-in |
| **Graph algorithms** | Built-in (BFS, DFS, etc.) | None | None | Basic |
| **Performance** | Good (Canvas, ~5K nodes) | Good for flows | Depends | Excellent (WebGL) |

### 3.4 Real-Time Updates — SSE

The frontend receives real-time progress via **Server-Sent Events**, not WebSocket:

```typescript
// usePipelineProgress hook
// For active turns: connects to SSE endpoint
// For completed turns: fetches Hatchet run snapshot via REST

// SSE endpoint: GET /api/v1/conversations/{id}/messages/{msgId}/stream
// Sends events like: node_created, scope_started, synthesis_started, done
```

This approach was chosen because:
- Hatchet natively supports streaming via `put_stream` — SSE is the natural fit
- No bidirectional communication needed (client only receives progress)
- Simpler than WebSocket for unidirectional event streams
- Automatic reconnection with `@microsoft/fetch-event-source`

### 3.5 Page Structure

| Route | Purpose |
|-------|---------|
| `/` | Home — start new conversation or browse existing |
| `/conversation/[id]` | Main research UI — chat + graph + pipeline progress |
| `/nodes`, `/nodes/[id]` | Browse/detail nodes in the knowledge graph |
| `/edges`, `/edges/[id]` | Browse/detail edges |
| `/facts`, `/facts/[id]` | Browse/detail facts |
| `/ingest` | File/link upload for knowledge ingestion |
| `/profile` | User profile settings |
| `/profile/tokens` | API token management |
| `/login`, `/register` | Authentication (route group) |
| `/auth/callback` | OAuth callback handler |

---

## 4. Database & Storage

### 4.1 Dual PostgreSQL Architecture

The system uses two PostgreSQL databases optimized for different workloads:

| Component | Port | Details |
|-----------|------|---------|
| **graph-db** | 5432 | Read-optimized. pgvector extension, FK constraints, junction tables (NodeFact, EdgeFact, DimensionFact). API and synthesis agent read from here. |
| **write-db** | 5435 | Write-optimized. No FKs, TEXT primary keys (deterministic), no deadlocks. Workers write here during pipelines. |
| **pgbouncer-read** | 5436 | Connection pool for graph-db reads |
| **pgbouncer-write** | 5434 | Connection pool for write-db writes |
| **Vector dimensions** | — | 3072 (text-embedding-3-large) |
| **Vector indexes** | — | HNSW for approximate nearest neighbor search (pgvector on graph-db) |

Deterministic UUIDs via `key_to_uuid()` (UUID5) bridge the two databases so both share identical IDs. The sync worker (`worker-sync`) polls write-db changes and propagates to graph-db.

### 4.1.1 Qdrant Vector Search

| Component | Details |
|-----------|---------|
| **Qdrant** | Dedicated vector database for semantic search (ports 6333-6334) |
| **Collections** | Separate collections for nodes and facts |
| **Usage** | Primary semantic search for both read and write paths; pgvector serves as fallback on graph-db |

### 4.2 Redis

| Use | Details |
|-----|---------|
| **General cache** | Redis-backed caching with configurable TTL |

### 4.3 Hatchet

| Component | Details |
|-----------|---------|
| **Hatchet Lite** | v0.79.22 — all-in-one workflow engine |
| **Own Postgres** | Separate PostgreSQL instance on port 5433 |
| **Ports** | 8888 (API), 8080 (UI), 7077 (gRPC) |
| **Token** | Generated via `just hatchet-token`, stored in `.env` |

### 4.4 Database Schema (Key Tables)

**graph-db (read-optimized) — `kt_db/models.py`:**
- `nodes` — Concepts, entities, events, locations, syntheses, supersyntheses. Vector embeddings (3072d).
- `node_counters` — Separate counter table (avoids row-lock contention).
- `edges` — Typed relationships (`related`, `cross_type`, `draws_from`). Canonical UUID ordering for undirected types. Unique constraint per (source, target, type).
- `facts` — Independent fact units with embeddings. Linked to nodes via `node_facts`.
- `raw_sources` — Original source data (URIs, content). Content-hash deduped.
- `fact_sources` — Links facts to their raw sources (provenance).
- `node_facts`, `edge_facts`, `dimension_facts` — Junction tables.
- `dimensions` — Multi-model analyses per node.
- `convergence_reports` — Cross-model agreement scores.
- `divergent_claims` — Where models disagree.
- `conversations`, `conversation_messages`, `research_reports` — Research sessions.
- `ingest_sources` — Uploaded files/links with processing status.
- `user`, `oauthaccount`, `api_tokens` — Authentication.
- `oauth_clients`, `oauth_authorization_codes`, `oauth_access_tokens`, `oauth_refresh_tokens` — OAuth 2.1 server.
- `system_settings`, `waitlist_entries`, `invites` — Admin/config.
- `llm_usage`, `llm_usage_records` — LLM usage tracking.

**write-db (write-optimized) — `kt_db/write_models.py`:**
- `write_nodes`, `write_edges`, `write_dimensions`, `write_facts`, `write_fact_sources` — Parallel to graph-db but with TEXT keys and no FKs.
- `write_seeds`, `write_seed_facts` — Seed entities (pre-node clustering).
- `write_edge_candidates` — Potential edges awaiting classification.
- `write_convergence_reports`, `write_divergent_claims`, `write_node_counters` — Worker-produced analysis.
- `sync_watermarks` — Tracks last synced timestamp per table for incremental sync.
- `sync_failures` — Dead-letter queue for failed sync entries.

**Metadata:**
- `ai_models`, `query_origins`, `provider_fetches`, `node_versions`
- `node_fact_rejections`, `fact_edge_evaluations` — Track evaluation history.

### 4.5 Schema Migrations

Alembic manages all schema changes. Migrations are committed to git.

```
backend/alembic/versions/    # Chronological migration files
```

---

## 5. External Services

### 5.1 Service Map

| Service | Used By | Auth | Notes |
|---------|---------|------|-------|
| **OpenRouter** | LiteLLM -> all model calls | API key | Multi-model routing |
| **Serper** | SerperProvider (default search) | API key | Google search results |
| **Brave Search API** | BraveSearchProvider | API key | Alternative search provider |
| **OpenAI** | Embeddings via LiteLLM | API key | text-embedding-3-large |
| **Wikidata** | Ontology module | No auth | SPARQL queries for taxonomy |
| **Google OAuth** | Auth module | Client ID/Secret | Social login |

### 5.2 Model Configuration

The system supports per-agent model and thinking-level overrides:

```python
# config.py — model overrides
default_model = "openrouter/x-ai/grok-4.1-fast"
decomposition_model = "openrouter/google/gemini-3.1-flash-lite-preview"
orchestrator_model = "openrouter/z-ai/glm-5"
# Thinking levels: "none", "low", "medium", "high"
decomposition_thinking_level = "low"
```

All model calls go through `ModelGateway` which wraps LiteLLM:
```python
gateway = ModelGateway()
response = await gateway.complete(messages=[...], role="decomposition")
# Automatically uses decomposition_model and decomposition_thinking_level
```

---

## 6. Project Structure

The backend is a **uv workspace monorepo** with 9 shared libraries (`libs/`) and 8 deployable services (`services/`).

```
knowledge-tree/
├── CLAUDE.md                        # Agent context (primary)
├── ARCHITECTURE.md                  # Requirements & architecture spec
├── STACK.md                         # This file
├── pyproject.toml                   # Root uv workspace definition
├── uv.lock                         # Single lockfile for all packages
├── docker-compose.yml               # Infrastructure + all services
├── justfile                         # Dev commands
├── .env                             # API keys, tokens, secrets
│
├── libs/                            # Shared libraries (9 packages)
│   ├── kt-config/                   # Settings, types, errors (kt_config)
│   ├── kt-db/                       # Models, repositories, session, alembic (kt_db)
│   ├── kt-models/                   # ModelGateway, embeddings, dimensions (kt_models)
│   ├── kt-providers/                # Serper, Brave, fetcher, registry (kt_providers)
│   ├── kt-graph/                    # GraphEngine, convergence, splitting (kt_graph)
│   ├── kt-facts/                    # Decomposition pipeline, extraction (kt_facts)
│   ├── kt-hatchet/                  # Hatchet client, lifespan, models (kt_hatchet)
│   └── kt-agents-core/              # BaseAgent, state, results (kt_agents_core)
│
├── services/                        # Deployable services (8 packages)
│   ├── api/                         # FastAPI REST + SSE (kt_api)
│   ├── worker-orchestrator/         # Exploration + synthesis (kt_worker_orchestrator)
│   ├── worker-search/               # Search workflow (kt_worker_search)
│   ├── worker-nodes/                # Node creation pipeline (kt_worker_nodes)
│   ├── worker-query/                # Query agent (kt_worker_query)
│   ├── worker-ingest/               # Ingest agent + pipeline (kt_worker_ingest)
│   ├── worker-conversations/        # Follow-up + resynthesize (kt_worker_conv)
│   └── worker-all/                  # Dev-mode all-in-one (kt_worker_all)
│
├── frontend/
│   └── src/
│       ├── app/                     # App Router pages
│       ├── components/              # UI components (auth, chat, pipeline, graph, node, etc.)
│       ├── contexts/                # React contexts (auth)
│       ├── hooks/                   # Custom hooks
│       ├── lib/                     # API client, utilities, tests
│       ├── types/                   # TypeScript types
│       └── config/                  # Model pricing data
│
├── wiki-frontend/                   # Alternative wiki-style UI (Astro)
└── config/                          # YAML configs (filters, models)
```

### 6.1 Dependency Rules

**Library dependencies (bottom-up):**
```
kt-config       (leaf — no kt deps)
kt-db           → kt-config
kt-models       → kt-config
kt-providers    → kt-config
kt-graph        → kt-db, kt-models, kt-config
kt-facts        → kt-config
kt-hatchet      → kt-config
kt-agents-core  → kt-config
```

**Service-to-service communication:**
- Workers NEVER import each other directly
- Cross-worker calls go through Hatchet workflow dispatch
- API lazily imports workflow objects for Hatchet dispatch
- `worker-all` aggregates all workflows for dev mode

No circular imports. Dependencies flow inward.

---

## 7. Testing Strategy

### 7.1 Backend Testing

**Framework:** pytest + pytest-asyncio + pytest-xdist + pytest-cov

**556+ tests** distributed across per-package `tests/` directories, running in parallel with per-worker PostgreSQL schema isolation.

```python
# Each package with integration tests has its own conftest.py
# Auth bypassed via os.environ.setdefault("SKIP_AUTH", "true")
```

#### Test Organization
Each package has its own `tests/` directory with optional `tests/integration/` subdirectory:
- `libs/kt-config/tests/` — Settings, types (14 tests)
- `libs/kt-models/tests/` — Gateway, dimensions (57 tests)
- `libs/kt-providers/tests/` — Providers, fetcher (32 tests)
- `libs/kt-graph/tests/` — Convergence, splitting (28 tests)
- `libs/kt-facts/tests/` — Decomposition pipeline (119 tests)
- `libs/kt-db/tests/integration/` — Repository tests (5 tests)
- `services/api/tests/` — API schemas, endpoints (21 tests)
- `services/worker-orchestrator/tests/` — Agent tools, explore scope (92 tests)
- `services/worker-nodes/tests/` — Node pipeline, edges (97 tests)
- `services/worker-query/tests/` — Query agent (31 tests)

### 7.2 Frontend Testing

**Framework:** Vitest + React Testing Library

**124+ tests** covering:
- Component rendering and interactions
- Custom hook behavior (renderHook)
- API client utilities
- Cost estimation logic
- Graph utilities

```bash
cd frontend && pnpm test              # Run all tests
cd frontend && pnpm lint              # ESLint
cd frontend && pnpm type-check        # TypeScript compiler check
```

---

## 8. Development Workflow

### 8.1 Local Setup

```bash
# Clone and enter
git clone <repo> && cd knowledge-tree

# Start infrastructure (Postgres + Redis + Hatchet)
just setup                           # docker compose up + generate Hatchet token

# Install all packages
uv sync --all-packages

# Run migrations
just migrate                         # alembic upgrade head (from libs/kt-db)

# API server (separate terminal)
just api-dev                         # FastAPI on port 8000

# Workers (separate terminal)
just worker                          # Start all Hatchet workers (dev mode)

# Frontend (separate terminal)
cd frontend
pnpm install
pnpm dev                              # Start Next.js dev server
```

### 8.2 docker-compose.yml

```yaml
services:
  # Infrastructure
  postgres:           # pgvector/pgvector:pg16, port 5432
  redis:              # redis:7-alpine, port 6379
  hatchet-db:         # postgres:16-alpine, port 5433 (Hatchet's own DB)
  hatchet:            # hatchet-lite:v0.79.22, ports 8888/8080/7077

  # Application services (for production-like deployment)
  api:                # services/api/Dockerfile, port 8000
  worker-orchestrator:  # services/worker-orchestrator/Dockerfile
  worker-search:      # services/worker-search/Dockerfile
  worker-nodes:       # services/worker-nodes/Dockerfile
  worker-query:       # services/worker-query/Dockerfile
  worker-ingest:      # services/worker-ingest/Dockerfile
  worker-conversations: # services/worker-conversations/Dockerfile
```

### 8.3 justfile Commands

```bash
# Infrastructure
just setup          # Start infra + generate Hatchet token
just up             # Start infrastructure only (postgres, redis, hatchet)
just up-all         # Start everything (infra + API + workers via Docker)
just down           # Stop all services
just clean          # Full reset (delete volumes, re-setup, re-migrate)

# Dev mode (no Docker for app services)
just api-dev        # Start API locally
just worker         # Start all Hatchet workers in one process
just worker-bottomup   # Bottom-up research worker only
just worker-search     # Search worker only
just worker-nodes      # Node pipeline worker only
just worker-synthesis  # Synthesis worker only
just worker-ingest     # Ingest worker only
just worker-sync       # Write-db → graph-db sync worker only

# Database
just migrate        # Run alembic migrations (from libs/kt-db)

# Testing
just test-libs      # Run all lib tests
just test-api       # Run API tests
just test-workers   # Run worker tests
just test-all       # Run all backend tests
just test-frontend  # Run frontend tests (lint + type-check + vitest)
```

### 8.4 Linting & Formatting

| Tool | Scope | Config |
|------|-------|--------|
| **ruff** | Python linting + formatting | `pyproject.toml [tool.ruff]` |
| **pyright** | Python type checking | `pyrightconfig.json` |
| **ESLint** | TypeScript/React linting | `eslint.config.mjs` |

### 8.5 Environment Variables

```bash
# .env (local development — never committed)
DATABASE_URL=postgresql+asyncpg://kt:localdev@localhost:5432/knowledge_tree
REDIS_URL=redis://localhost:6379/0

# AI / Search
OPENROUTER_API_KEY=sk-or-...
OPENAI_API_KEY=sk-...
SERPER_KEY=...
BRAVE_KEY=BSA...

# Hatchet
HATCHET_CLIENT_TOKEN=...           # Generated via `just hatchet-token`

# Auth
JWT_SECRET_KEY=change-me-in-production
GOOGLE_OAUTH_CLIENT_ID=...
GOOGLE_OAUTH_CLIENT_SECRET=...
SKIP_AUTH=false                    # Set true in tests

LOG_LEVEL=INFO
```

---

## 9. Configuration Management

### 9.1 Application Config (Pydantic Settings)

`kt_config/settings.py` has grown to support extensive per-agent model overrides, thinking levels, and feature flags:

```python
class Settings(BaseSettings):
    # Database + pool
    database_url: str
    db_pool_size: int = 30
    db_max_overflow: int = 60

    # Model routing
    default_model: str = "openrouter/x-ai/grok-4.1-fast"
    decomposition_model: str = "openrouter/google/gemini-3.1-flash-lite-preview"
    orchestrator_model: str = "openrouter/z-ai/glm-5"
    synthesis_model: str = ""  # empty = use default_model

    # Thinking levels per role
    decomposition_thinking_level: str = "low"
    orchestrator_thinking_level: str = ""

    # Embeddings
    embedding_model: str = "openrouter/openai/text-embedding-3-large"
    embedding_dimensions: int = 3072

    # Feature flags
    use_hatchet: bool = True
    enable_full_text_fetch: bool = True
    enable_semantic_expansion: bool = True

    # Auth
    jwt_secret_key: str = "change-me-in-production"
    skip_auth: bool = False
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
```

### 9.2 Filter & Model Configs (YAML)

```
config/
├── filters/
│   ├── default.yaml
│   └── primary_sources_only.yaml
└── models.yaml
```

---

## 10. Deployment

### 10.1 Target Architecture

```
                     +----------------+
                     |   CDN/Edge     |
                     | (Vercel/CF)    |
                     +-------+--------+
                             |
                     +-------v--------+
                     |   Next.js      |  Static + SSR
                     |  (frontend)    |
                     +-------+--------+
                             | REST + SSE
                     +-------v--------+
                     |   FastAPI      |  Stateless API
                     |   (kt_api)     |
                     +--+------+--+---+
                        |      |  |
                 +------v-+ +--v--v---+
                 |Postgres | | Hatchet |  Workflow engine
                 |+pgvector| | +Redis  |
                 +---------+ +----+----+
                                  |
              +-------------------+-------------------+
              |            |           |               |
         +----v---+  +----v----+  +---v-----+  +------v-------+
         |worker- |  |worker-  |  |worker-  |  |worker-       |
         |orch    |  |nodes    |  |query    |  |conversations |
         +--------+  +---------+  +---------+  +--------------+
              (+ worker-search, worker-ingest)
```

Each worker is a separate Docker container (via `services/*/Dockerfile`), independently scalable.

### 10.2 Deployment Options

| Component | Option A (Simple) | Option B (Scalable) |
|-----------|------------------|-------------------|
| Frontend | Vercel | Vercel |
| API | Railway / Render | Kubernetes |
| Workers | Railway / Render (1 container each) | Kubernetes (auto-scale per worker type) |
| Postgres | Railway / Supabase | Cloud SQL / RDS with pgvector |
| Hatchet | Hatchet Cloud | Self-hosted Hatchet |
| Redis | Upstash | ElastiCache |

The API and workers are stateless (all state in Postgres + Hatchet). Each worker type (`services/worker-*/Dockerfile`) scales independently. `docker compose up -d` boots everything locally. Start with Option A, migrate to B when needed.
