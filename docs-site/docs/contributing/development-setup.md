---
sidebar_position: 5
title: Development Setup
---

# Development Setup

## Prerequisites

- **Python 3.12+** with [uv](https://docs.astral.sh/uv/) installed
- **Node.js 20+** with [pnpm](https://pnpm.io/) installed
- **Docker** and Docker Compose
- **just** command runner ([casey/just](https://github.com/casey/just))

## Initial setup

```bash
# Clone the repository
git clone git@github.com:openktree/knowledge-tree.git
cd knowledge-tree

# Install all Python packages
uv sync --all-packages

# Start infrastructure (PostgreSQL, Redis, Hatchet)
just setup

# Run database migrations
just migrate

# Install frontend dependencies
cd frontend && pnpm install
```

## Running the stack

You need 3-4 terminal windows:

```bash
# Terminal 1 — API
just api-dev                    # FastAPI on port 8000

# Terminal 2 — Workers (all-in-one for dev)
just worker                     # All Hatchet workers

# Terminal 3 — Frontend
cd frontend && pnpm dev         # Next.js on port 3000

# Terminal 4 (optional) — Wiki
cd wiki-frontend && pnpm dev    # Astro on port 4321
```

## Package manager rules

:::warning Critical
- **Python:** Always use `uv`. Never `pip`, `pip install`, or `poetry`.
- **Node.js:** Always use `pnpm`. Never `npm`, `yarn`, or `bun`.
:::

```bash
# Python — correct
cd libs/kt-config && uv add <package>
uv run --project libs/kt-config pytest -x -v

# Node.js — correct
cd frontend && pnpm add <package>
cd frontend && pnpm dev
```

## Running tests

```bash
# Backend — specific package
uv run --project libs/kt-facts pytest libs/kt-facts/tests/ -x -v
uv run --project services/api pytest services/api/tests/ -x -v

# Backend — all tests
just test-all

# Frontend
cd frontend && pnpm lint && pnpm type-check && pnpm test
```

**Every code change must be verified with tests before completion.**

## Environment variables

API keys and secrets live in `.env` at the project root:

| Variable | Purpose |
|----------|---------|
| `OPENROUTER_API_KEY` | Multi-model AI gateway |
| `OPENAI_API_KEY` | Embeddings |
| `SERPER_KEY` | Search provider |
| `BRAVE_KEY` | Search provider (alternative) |
| `HATCHET_CLIENT_TOKEN` | Workflow orchestration (auto-generated via `just hatchet-token`) |
| `JWT_SECRET_KEY` | Authentication |
| `GOOGLE_OAUTH_CLIENT_ID` | Google OAuth |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Google OAuth |
| `DATABASE_URL` | Graph-db connection |
| `WRITE_DATABASE_URL` | Write-db connection |

Never hardcode API keys. All config flows through `kt_config.settings.Settings`.

## Useful commands

```bash
just setup          # Start infra + generate Hatchet token
just up             # Start infrastructure only
just up-all         # Start everything (infra + API + workers)
just down           # Stop all services
just clean          # Full reset (delete volumes, re-setup, re-migrate)
just migrate        # Run database migrations
just test-libs      # Run all lib tests
just test-api       # Run API tests
just test-workers   # Run worker tests
just test-all       # Run all backend tests
just test-frontend  # Run frontend tests
```

## Commit conventions

All commits use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>
```

**Types:** `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`

**Scopes:** Package or area affected — `kt-config`, `kt-db`, `api`, `worker-synthesis`, `frontend`, etc.

**Examples:**
```
feat(worker-synthesis): add super-synthesizer agent
fix(kt-db): handle missing node in get_by_id
test(frontend): add unit tests for graph visualizer
docs: update MCP tool reference
```

## Branch workflow

Every change goes on a feature branch with a pull request:

```bash
git checkout -b feat/my-change main
# ... make changes, run tests ...
git add -A && git commit -m "feat(scope): description"
git push -u origin feat/my-change
gh pr create --fill
gh pr checks <pr-number> --watch   # Wait for CI to pass
```

A task is not complete until the PR exists and all CI pipelines are green.
