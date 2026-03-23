# Knowledge Tree — Wiki Frontend

Read-only wiki-style browser for the Knowledge Tree graph. Each node in the graph gets its own page showing its definition, model perspectives, related nodes, and source facts — like Wikipedia for your knowledge graph.

Built with [Astro 5](https://astro.build) (SSR) + plain CSS. Runs on port **4321**.

## Prerequisites

- Node.js 18+ and pnpm
- Knowledge Tree backend running at `localhost:8000`
- Backend started with `SKIP_AUTH=true` **or** an API token (see Auth below)

## Quick start

```bash
cd wiki-frontend
pnpm install
pnpm dev
```

Open [http://localhost:4321](http://localhost:4321).

## Auth

The backend requires authentication on all routes. Two options:

**Option A — dev mode (no token)**

Start the backend with `SKIP_AUTH=true`:

```bash
SKIP_AUTH=true uv run uvicorn knowledge_tree.main:app --reload --port 8000
```

**Option B — API token**

Generate a long-lived token via the backend, then pass it to the wiki:

```bash
API_TOKEN=<your-token> pnpm dev
```

If the backend returns 401, the page displays a clear error message.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `API_BASE_URL` | `http://localhost:8000` | Backend base URL |
| `API_TOKEN` | _(unset)_ | Bearer token for auth (omit when using `SKIP_AUTH=true`) |

Set them in `wiki-frontend/.env` (copy from `.env.example`):

```bash
cp .env.example .env
# then edit .env with your values
```

## Just commands

```bash
just dev      # Start dev server on :4321
just build    # Production build
just preview  # Preview production build
just check    # Type-check all Astro files
just install  # Install dependencies
```

## Structure

```
src/
├── lib/api.ts              # Typed API client
├── types/index.ts          # TypeScript types mirrored from backend schemas
├── styles/global.css       # Wiki-style CSS
├── layouts/WikiLayout.astro
└── pages/
    ├── index.astro         # Node listing + search
    └── nodes/[id].astro    # Individual node wiki page
```
