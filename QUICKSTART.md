# Quickstart

Run Knowledge Tree locally with Docker Compose using pre-built images. No build step required.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) (with Docker Compose v2)
- An [OpenRouter API key](https://openrouter.ai/keys) (required for LLM calls)
- A search provider API key: [Serper](https://serper.dev/) (recommended) or [Brave Search](https://brave.com/search/api/)

## Setup

### 1. Create your `.env` file

```bash
cp .env.example .env
```

Edit `.env` and add your API keys:

```env
OPENROUTER_API_KEY=sk-or-...     # Required — powers all LLM calls
SERPER_KEY=...                    # Recommended — default search provider
```

### 2. Start Knowledge Tree

```bash
./scripts/quickstart-init.sh
```

This script will:
1. Start all infrastructure (PostgreSQL, Redis, Qdrant, Hatchet)
2. Generate a Hatchet workflow token and save it to `.env`
3. Run database migrations
4. Start the API, workers, and frontend

### 3. Open the app

- **Frontend:** http://localhost:3000
- **API:** http://localhost:8000
- **API docs:** http://localhost:8000/docs

Register your first account. The **first user is automatically promoted to admin** and can manage settings, invites, and other users.

## Usage

Once running, you can:

1. **Ingest sources** — paste links or upload documents to feed the knowledge graph
2. **Explore the graph** — browse nodes, facts, dimensions, and relationships
3. **Create syntheses** — generate research documents that weave evidence into analytical narratives

## Managing the stack

```bash
# View logs
docker compose -f docker-compose.quickstart.yml logs -f

# View logs for a specific service
docker compose -f docker-compose.quickstart.yml logs -f api

# Stop all services
docker compose -f docker-compose.quickstart.yml down

# Stop and remove all data (fresh start)
docker compose -f docker-compose.quickstart.yml down -v

# Restart after stopping
docker compose -f docker-compose.quickstart.yml up -d
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| Frontend | 3000 | Next.js research interface |
| API | 8000 | FastAPI REST + SSE |
| PostgreSQL (graph) | - | Read-optimized graph database (pgvector) |
| PostgreSQL (write) | - | Write-optimized database |
| Redis | - | Caching |
| Qdrant | - | Vector search |
| Hatchet | - | Workflow orchestration |
| Workers (6) | - | Background processing (search, ingest, nodes, synthesis, sync, bottomup) |

Infrastructure ports are not exposed by default. If you need direct access for debugging, use the development `docker-compose.yml` instead.

## Troubleshooting

### Services fail to start

Check that Hatchet is healthy before other services start:

```bash
docker compose -f docker-compose.quickstart.yml logs hatchet
```

If the Hatchet token wasn't generated, re-run the init script:

```bash
./scripts/quickstart-init.sh
```

### Migrations fail

If the `migrate` service fails, check database connectivity:

```bash
docker compose -f docker-compose.quickstart.yml logs migrate
```

### Out of memory

Workers have memory limits. If you see OOM kills, increase Docker's memory allocation (recommend 8 GB+).

## Updating

Pull the latest images and restart:

```bash
docker compose -f docker-compose.quickstart.yml pull
docker compose -f docker-compose.quickstart.yml up -d
```

If there are database schema changes, the `migrate` service runs automatically on startup.
