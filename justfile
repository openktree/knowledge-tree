
# ── Dev mode (no Docker for workers) ──────────────────────────────

# Start all Hatchet workers in one process (dev mode)
worker:
    uv run --project services/worker-all -m kt_worker_all

# Start individual worker types (dev mode)
worker-bottomup:
    uv run --project services/worker-bottomup -m kt_worker_bottomup

worker-search:
    uv run --project services/worker-search -m kt_worker_search

worker-nodes:
    uv run --project services/worker-nodes -m kt_worker_nodes

worker-ingest:
    uv run --project services/worker-ingest -m kt_worker_ingest

worker-synthesis:
    uv run --project services/worker-synthesis -m kt_worker_synthesis

worker-sync:
    uv run --project services/worker-sync -m kt_worker_sync

# Start docs site locally
docs-dev:
    cd docs-site && pnpm dev

# Start landing page locally
landing-dev:
    cd landing-page && pnpm dev

# Start API locally (no Docker)
api-dev:
    uv run --project services/api uvicorn kt_api.main:app --reload --port 8000

# Start MCP server locally (no Docker)
mcp-dev:
    uv run --project services/mcp uvicorn kt_mcp.server:app --reload --port 8001

# ── Infrastructure ────────────────────────────────────────────────

# Full local setup: start infra, generate token
setup: up hatchet-token

# Start infrastructure only (postgres, postgres-write, redis, hatchet)
up:
    docker compose up -d postgres postgres-write pgbouncer-write redis qdrant hatchet

# Start everything including app services (Docker)
up-all:
    docker compose up -d

# Stop all services
down:
    docker compose down

# Full reset (delete volumes, re-setup, re-migrate)
clean:
    docker compose down -v
    @just setup
    @just migrate

# Generate a Hatchet API token and write it to .env
hatchet-token:
    #!/usr/bin/env bash
    set -euo pipefail

    echo "Waiting for Hatchet to be ready..."
    until docker compose exec -T hatchet wget -q --spider http://localhost:8080/api/live 2>/dev/null; do
        sleep 2
    done

    TOKEN="$(docker compose exec -T hatchet /hatchet-admin token create \
        --config /config \
        --tenant-id 707d0855-80ab-4e1f-a156-f1c4546cbf52 2>/dev/null | tr -d '[:space:]')"

    if [ -z "$TOKEN" ]; then
        echo "ERROR: Failed to generate Hatchet token"
        exit 1
    fi

    # Update .env — replace existing HATCHET_CLIENT_TOKEN line or append
    if grep -q '^HATCHET_CLIENT_TOKEN=' .env 2>/dev/null; then
        sed -i "s|^HATCHET_CLIENT_TOKEN=.*|HATCHET_CLIENT_TOKEN=${TOKEN}|" .env
    else
        echo "HATCHET_CLIENT_TOKEN=${TOKEN}" >> .env
    fi

    echo "Hatchet token written to .env"

# ── Database ──────────────────────────────────────────────────────

# Run database migrations (both graph-db and write-db)
migrate:
    cd libs/kt-db && uv run alembic upgrade head
    cd libs/kt-db && uv run alembic -c alembic_write.ini upgrade head

# Run write-db migrations only
migrate-write:
    cd libs/kt-db && uv run alembic -c alembic_write.ini upgrade head

# ── Testing ───────────────────────────────────────────────────────

# Run all lib tests
test-libs:
    cd libs/kt-config && uv run pytest -x
    cd libs/kt-db && uv run pytest -x
    cd libs/kt-models && uv run pytest -x
    cd libs/kt-providers && uv run pytest -x
    cd libs/kt-graph && uv run pytest -x
    cd libs/kt-facts && uv run pytest -x

# Run API tests
test-api:
    cd services/api && uv run pytest -x

# Run MCP tests
test-mcp:
    cd services/mcp && uv run pytest -x

# Run worker tests
test-workers:
    cd services/worker-bottomup && uv run pytest -x
    cd services/worker-search && uv run pytest -x
    cd services/worker-nodes && uv run pytest -x
    cd services/worker-ingest && uv run pytest -x

# Run all tests
test-all: test-libs test-api test-workers

# Frontend tests
test-frontend:
    cd frontend && pnpm lint && pnpm type-check && pnpm test

# ── k3d Local Cluster ───────────────────────────────────────────

# Create k3d cluster
k3d-create:
    k3d cluster create --config infra/local/k3d.yaml

# Delete k3d cluster
k3d-delete:
    k3d cluster delete knowledge-tree-local

# Build and push all images to k3d registry
k3d-build:
    ./scripts/k3d-build.sh

# Build and push specific service(s): just k3d-build-svc api worker-sync
k3d-build-svc +services:
    ./scripts/k3d-build.sh {{services}}

# Deploy to k3d (install or upgrade)
k3d-deploy:
    #!/usr/bin/env bash
    set -euo pipefail
    if helm status kt >/dev/null 2>&1; then
        helm upgrade kt helm/knowledge-tree/ -f infra/local/values.yaml --set cnpg-operator.enabled=false
    else
        helm install cnpg-operator cloudnative-pg/cloudnative-pg --version 0.23.0 --namespace cnpg-system --create-namespace --wait || true
        helm install kt helm/knowledge-tree/ -f infra/local/values.yaml --set cnpg-operator.enabled=false
    fi

# Full k3d setup: create cluster, build images, deploy
k3d-up: k3d-create k3d-build k3d-deploy
