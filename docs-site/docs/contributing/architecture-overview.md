---
sidebar_position: 1
title: Architecture Overview
---

# Architecture Overview

Knowledge Tree is a **uv workspace monorepo** split into shared libraries (`libs/`) and deployable services (`services/`). The system uses a dual-database architecture optimized for different workloads.

## Monorepo structure

```
open-knowledge-tree/
  libs/                   # Shared Python libraries (no agent/LLM logic)
    kt-config/            # Settings, enums, errors
    kt-db/                # SQLAlchemy models, repositories, migrations
    kt-models/            # AI model gateway, embeddings
    kt-providers/         # Knowledge providers (Serper, Brave)
    kt-graph/             # GraphEngine — CRUD, search, convergence
    kt-facts/             # Fact decomposition, extraction, dedup
    kt-hatchet/           # Hatchet client, workflow I/O models
    kt-agents-core/       # Base agent classes
    kt-qdrant/            # Qdrant vector search
  services/               # Deployable microservices
    api/                  # FastAPI REST + SSE
    worker-bottomup/      # Bottom-up discovery
    worker-synthesis/     # Synthesis agents
    worker-nodes/         # Node creation pipeline
    worker-ingest/        # File/link ingestion
    worker-search/        # Standalone search
    worker-sync/          # Write-db → graph-db sync
    mcp/                  # MCP server
  frontend/               # Next.js 16 research UI
  wiki-frontend/          # Astro wiki browser
  helm/                   # Kubernetes Helm charts
  docker-compose.yml      # Local development stack
```

## Dual-database architecture

The system uses **two PostgreSQL databases** optimized for different workloads:

### Graph-db (read-optimized)

- **pgvector** extension for embedding storage and vector search
- **Foreign key constraints** for referential integrity
- **Junction tables** (NodeFact, EdgeFact, DimensionFact) for many-to-many relationships
- The API reads from graph-db
- **Only the sync worker writes to graph-db** — no other service writes directly

### Write-db (write-optimized)

- **No foreign keys** — eliminates FK-validation deadlocks
- **Deterministic TEXT primary keys** based on node type + concept name
- All agent pipelines write here during processing
- Normalized structure for fast, conflict-free writes

### Bridging the databases

**Deterministic UUIDs** connect both databases: `key_to_uuid(write_key)` uses UUID5 to derive the same UUID from a write-db TEXT key. Both databases share identical IDs for every entity.

```python
# libs/kt-db/src/kt_db/keys.py
def key_to_uuid(key: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, key)
```

### The Sync Worker

The **sync worker** (`worker-sync`) is the sole writer to graph-db. It polls write-db for changes (`updated_at > watermark`), upserts into graph-db, and creates junction rows. This single-writer design eliminates write contention on the graph database.

## Hatchet workflow orchestration

All heavy processing runs as **durable Hatchet workflows** — not in-process or via simple background tasks. Hatchet provides:

- DAG-based task dependencies (e.g., create_node -> dimensions -> definition -> parent)
- Fan-out/fan-in for parallel operations
- Progress streaming via SSE
- Workflow monitoring UI
- Automatic retries and durability

### Key workflows

| Workflow | Service | Purpose |
|----------|---------|---------|
| `ingest_build_wf` | worker-ingest | Source ingestion pipeline |
| `bottom_up_wf` | worker-bottomup | Bottom-up scope exploration |
| `node_pipeline_wf` | worker-nodes | Node creation DAG |
| `synthesizer_wf` | worker-synthesis | Synthesis document creation |
| `super_synthesizer_wf` | worker-synthesis | Multi-scope super-synthesis |
| `search_wf` | worker-search | Standalone search |

## Architectural boundaries

| Rule | Reason |
|------|--------|
| Workers never import each other at module level | Prevents circular deps, enables independent deployment |
| Libs never import from services | Libs are shared foundations |
| Agent/LLM logic never goes in libs | Only base classes in `kt-agents-core`; implementations in workers |
| API never contains agent logic | API dispatches Hatchet workflows and reads results |
| All worker-to-worker communication via Hatchet | Workers dispatch workflows by name string only |
| New DB models/migrations only in kt-db | Single source of schema truth |
