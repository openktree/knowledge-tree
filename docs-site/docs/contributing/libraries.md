---
sidebar_position: 4
title: Libraries
---

# Shared Libraries

Shared libraries live in `libs/` and contain reusable infrastructure with **no agent or LLM logic**. Each is a uv workspace package following the `src/kt_<name>/` layout.

## kt-config

**Settings, enums, and errors.** The central configuration package.

| Module | Purpose |
|--------|---------|
| `settings.py` | `Settings` class (Pydantic BaseSettings), loads from `.env` |
| `types.py` | Shared enums: `NodeType`, `FactType`, `EdgeType` |
| `errors.py` | Custom exception classes |

All configuration flows through `get_settings()`. Never hardcode API keys or connection strings.

## kt-db

**Database models, repositories, and migrations.** The single source of schema truth.

| Directory | Purpose |
|-----------|---------|
| `models.py` | Graph-db SQLAlchemy models (Node, Edge, Fact, Dimension, etc.) |
| `write_models.py` | Write-db models (WriteNode, WriteEdge, WriteSeed, etc.) |
| `keys.py` | `make_node_key()`, `make_edge_key()`, `key_to_uuid()` |
| `repositories/` | Repository classes for all database access |
| `alembic/` | Graph-db migrations |
| `alembic_write/` | Write-db migrations |

**Rule:** All database access goes through repository classes. No raw SQL queries elsewhere.

## kt-models

**AI model gateway and embeddings.**

- `ModelGateway` — Unified interface to 100+ models via LiteLLM + OpenRouter
- `EmbeddingService` — Text embedding via `text-embedding-3-large` (3072 dimensions)
- Per-agent model overrides and thinking level configuration

## kt-providers

**Knowledge providers** — external data sources.

- `KnowledgeProvider` abstract base class
- `SerperProvider` — Serper search API (default)
- `BraveProvider` — Brave Search API
- New providers implement the ABC with a single `search()` method

## kt-graph

**Graph operations and search.**

- `GraphEngine` — Node/edge CRUD, search, convergence scoring
- Routes writes to write-db, reads from graph-db
- Maintains an in-memory node cache for pipeline-created nodes
- Qdrant integration for vector search

## kt-facts

**Fact extraction and processing.**

| Module | Purpose |
|--------|---------|
| `decomposition/` | Source segmentation, fact extraction pipeline |
| `processing/entity_extraction.py` | Per-fact entity and concept extraction |
| `processing/seed_extraction.py` | Seed creation and management |
| `processing/seed_routing.py` | Fact routing to disambiguated seeds |
| `processing/seed_heuristics.py` | Disambiguation strategies |
| `dedup.py` | Fact deduplication by embedding similarity |

## kt-hatchet

**Hatchet workflow client.**

- `get_hatchet()` — Singleton Hatchet client
- Workflow I/O models (Pydantic) for type-safe workflow dispatch
- Worker state management

## kt-agents-core

**Base agent classes** shared across worker services.

- `BaseAgent` — Template-method pattern for LangGraph agent wiring
- Shared state models for agent state
- Tool result types

**Rule:** Only base classes go here. All agent implementations, prompts, and tools belong in their worker service.

## kt-qdrant

**Qdrant vector search repositories.**

- Node embedding search
- Seed embedding search and re-embedding
- Fact similarity search for deduplication

## Adding a new library

Create a new lib only when you have shared infrastructure that:
1. Has **no agent/LLM logic**
2. Is needed by **2+ packages**
3. Doesn't fit in any existing lib

Copy structure from an existing lib (e.g., `kt-qdrant`), then run `uv sync --all-packages`.
