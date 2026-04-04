---
sidebar_position: 10
title: "ADR-001: GraphEngine Split"
---

# ADR-001: Split GraphEngine into WorkerGraphEngine and ReadGraphEngine

**Status:** Accepted (2026-04-03)

## Context

Knowledge Tree uses a dual-database architecture:

- **graph-db** (PostgreSQL + pgvector) -- read-optimized, used by the API, wiki frontend, and MCP server for serving user queries.
- **write-db** (PostgreSQL) -- write-optimized, no FK constraints, used by Hatchet worker pipelines for building the graph.

The original `GraphEngine` class was a single ~2000-line god object that mixed graph-db reads and write-db writes with runtime fallback paths. This caused three production issues:

### 1. Connection pool starvation

Hatchet worker tasks (bottom-up discovery, node pipeline, ingest) opened graph-db sessions they did not actually need for writes. With `pool_size=10` and `max_overflow=20`, a few concurrent graph-building tasks exhausted the graph-db pool. The API, wiki, and MCP server -- which serve user-facing reads from graph-db -- were starved, causing `/nodes` page timeouts and degraded wiki performance during active graph growth.

### 2. Stale connections during LLM calls

The `seed_dedup_task` wrapped an entire batch of seeds in a single `session.begin()` transaction. Inside the loop, each seed's deduplication called external APIs (embedding via OpenRouter ~120s, LLM confirmation ~120s). The write-db connection sat idle in a transaction during these calls. PGBouncer (transaction mode) or PostgreSQL could kill the connection, causing `ConnectionDoesNotExistError`.

### 3. Poisoned transactions

`auto_build` phases (`_promote_seeds`, `_absorb_merged_nodes`, `_create_cooccurrence_edges`) opened one session per batch, caught exceptions per-item with bare `except Exception`, but never rolled back. After one item failed, PostgreSQL marked the transaction as aborted. All subsequent items failed with `InFailedSQLTransactionError`.

### Root cause

Nothing prevented a worker from accidentally opening a graph-db session. The fallback logic in `GraphEngine` made this easy to miss -- if `write_session` happened to be `None`, methods silently fell through to graph-db writes.

## Decision

Replace the single `GraphEngine` class with two purpose-built subclasses.

### WorkerGraphEngine (write-db + Qdrant only)

- **Constructor:** `WorkerGraphEngine(write_session, embedding_service, qdrant_client)`
- **No graph-db session parameter** -- physically impossible to open a graph-db connection.
- All reads served from write-db tables (`WriteNode`, `WriteFact`, `WriteEdge`, `WriteDimension`) and Qdrant vector search.
- All writes go to write-db, committed immediately.
- **Used by:** `worker-bottomup`, `worker-search`, `worker-nodes`, `worker-ingest`. Synthesis workers use this for writing synthesis nodes after the agent finishes.

### ReadGraphEngine (graph-db + Qdrant, read-only)

- **Constructor:** `ReadGraphEngine(session=..., qdrant_client=...)` or `ReadGraphEngine(session_factory=..., qdrant_client=...)`
- **No write-db session** -- cannot accidentally write to write-db.
- `session_factory` mode opens short-lived sessions per method call instead of holding one for 30 minutes (used by the synthesis agent during navigation).
- Admin write methods (`create_node`, `update_node`, `delete_node`, etc.) write directly to graph-db for API admin operations.
- **Used by:** API, MCP server, wiki-frontend queries. Synthesis workers use this for graph reads during the 30-minute agent loop.

### Supporting changes

- **PGBouncer for graph-db:** New `pgbouncer-read` service in docker-compose (and helm chart) to pool graph-db connections.
- **`pool_recycle`:** Added to all SQLAlchemy engines (1800s graph-db, 600s write-db) to prevent stale connections.
- **PGBouncer timeouts:** `SERVER_IDLE_TIMEOUT=600`, `SERVER_LIFETIME=3600` on both bouncers.
- **Graph-db pool increase:** `pool_size` 10 to 20, `max_overflow` 20 to 40, `max_connections` 100 to 200.
- **Per-seed transactions** in seed_dedup instead of one wrapping the whole batch.
- **`begin_nested()` savepoints** in auto_build so individual item failures do not poison the batch transaction.

## Consequences

### Positive

- **Graph-db pool is free during graph growth.** Workers never touch graph-db. API/wiki/MCP performance is unaffected by background pipelines.
- **Compile-time safety.** A worker importing `WorkerGraphEngine` physically cannot open a graph-db connection -- the parameter does not exist. No more runtime fallback surprises.
- **Shorter connection hold times.** Synthesis reads use `session_factory` mode with per-call sessions instead of holding one connection for 30 minutes.
- **Better error isolation.** Savepoints in auto_build prevent one failed seed from cascading to the entire batch.

### Negative

- **Two classes to maintain.** Methods shared between both engines (e.g., `compute_richness`, `is_node_stale`) are duplicated. A base class could be extracted later if this becomes a maintenance burden.
- **Write-db reads may lag behind graph-db.** Workers read from write-db which is ahead of graph-db (sync worker has not propagated yet). This is intentional -- workers see their own writes immediately. API reads from graph-db which is the source of truth for users.
- **Test complexity.** Integration tests that previously used the unified `GraphEngine` now need to choose the correct engine type. Tests for worker pipelines use `WorkerGraphEngine`; tests for API behavior use `ReadGraphEngine`.

## Migration notes for contributors

| Context | Import | Constructor |
|---|---|---|
| Workers | `from kt_graph.worker_engine import WorkerGraphEngine` | `WorkerGraphEngine(write_session, embedding_service, qdrant_client)` |
| API / MCP | `from kt_graph.read_engine import ReadGraphEngine` | `ReadGraphEngine(session=..., qdrant_client=...)` |
| Synthesis (reads) | `from kt_graph.read_engine import ReadGraphEngine` | `ReadGraphEngine(session_factory=..., qdrant_client=...)` |
| Synthesis (writes) | `from kt_graph.worker_engine import WorkerGraphEngine` | `WorkerGraphEngine(write_session, ...)` |

- **`AgentContext.graph_engine`** is now typed as `WorkerGraphEngine | ReadGraphEngine`. Agent tools work with either since both expose the same read method signatures.
- **Never import `kt_graph.engine`** -- the old module is deleted.
