---
sidebar_position: 3
title: Services
---

# Services

Each deployable service runs as an independent process. Workers communicate exclusively through Hatchet workflow dispatch.

## API (`services/api/`)

**Port:** 8000 | **Framework:** FastAPI + uvicorn

The REST + SSE API layer. Handles authentication, request routing, and real-time progress streaming. The API **never contains agent logic** — it dispatches Hatchet workflows and reads results.

Key responsibilities:
- JWT + Google OAuth authentication via `fastapi-users`
- API token management for programmatic access
- SSE streaming for workflow progress
- CRUD endpoints for nodes, edges, facts, syntheses, sources

**Routes:** Organized per-resource in `kt_api/`. All routes require auth except auth endpoints.

## worker-bottomup (`services/worker-bottomup/`)

Bottom-up discovery workflows. When users ingest sources, this worker:
- Extracts scope and topic from search results
- Selects which nodes to create or expand
- Plans exploration scopes

**Workflow:** `bottom_up_wf`

## worker-synthesis (`services/worker-synthesis/`)

Synthesis document creation. Contains the two main agent implementations:

- **SynthesizerAgent** — Navigates the graph with an exploration budget, produces a research document with fact citations
- **SuperSynthesizerAgent** — Orchestrates multiple synthesizer runs, combines into a meta-narrative

**Workflows:** `synthesizer_wf`, `super_synthesizer_wf`

## worker-nodes (`services/worker-nodes/`)

The node creation pipeline. Runs as a DAG:

```
create_node → dimensions → definition
```

1. **create_node** — Creates the node in write-db with linked facts
2. **dimensions** — Runs multi-model analysis (parallel across configured models)
3. **definition** — Synthesizes a definition from dimensions

**Workflow:** `node_pipeline_wf`

## worker-ingest (`services/worker-ingest/`)

File and link ingestion. Handles:
- File upload processing (PDF, DOCX, text)
- URL fetching via `trafilatura`
- Content extraction and segmentation
- Fact decomposition and seed creation

**Workflow:** `ingest_build_wf`

## worker-search (`services/worker-search/`)

Standalone search workflow for finding existing nodes and facts in the graph.

**Workflow:** `search_wf`

## worker-sync (`services/worker-sync/`)

The **sole writer to graph-db**. Bridges write-db and graph-db via incremental sync:

1. Polls write-db tables by `updated_at > watermark`
2. Upserts into graph-db
3. Creates junction rows (NodeFact, EdgeFact, DimensionFact) from stored `fact_ids` arrays
4. Updates watermarks in `sync_watermarks` table

**Must run as a single replica** — watermark-based, single-threaded by design.

## MCP (`services/mcp/`)

**Port:** 8001 | **Framework:** FastMCP

Read-only MCP server exposing 8 tools for knowledge graph navigation. See [MCP Integration](/mcp/overview) for full documentation.

## worker-all (`services/worker-all/`)

Development convenience — registers all workflows in a single process. Not used in production.

```bash
just worker  # Starts worker-all for local dev
```
