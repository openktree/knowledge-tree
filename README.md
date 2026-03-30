# Knowledge Tree

Knowledge Tree is a **knowledge integration system** that builds understanding exclusively from raw external data. It constructs and evolves a knowledge graph where every node is grounded in provenance-tracked facts decomposed from real sources. It aims to make the underlying data and sources visible enabling the comprehension, understanding and integration of complex data and seemingly opposing ideas.

> **Early Development Notice:** Knowledge Tree is currently under active development and not yet generally available. Features may change, and the system may experience downtime or instability. You can **join the waitlist** on the [Research App](https://research.openktree.com) to get early access.

## What makes Knowledge Tree different

- **Knowledge from data, not from models.** AI models are reasoning engines, not knowledge sources. All knowledge traces back to external raw data.
- **Integration, not ignoring.** The system never discards coherent information. Contradictory facts produce perspectives with alternate viewpoints, not suppression.
- **Multi-model convergence.** Multiple AI models analyze the same evidence independently. Consensus reveals genuine truth; divergence reveals where biases determine conclusions.
- **Transparent provenance.** Every claim traces through facts back to original sources. Nothing is hidden.
- **Accumulation.** The graph improves with every query. Frequently explored topics become deeply supported over time.

## Services

| Service | URL | Description |
|---------|-----|-------------|
| **Landing Page** | [openktree.com](https://openktree.com) | Project overview and links |
| **Research App** | [research.openktree.com](https://research.openktree.com) | Ingest sources, create syntheses, explore the graph |
| **Wiki** | [wiki.openktree.com](https://wiki.openktree.com) | Read-only knowledge graph browser |
| **Docs** | [docs.openktree.com](https://docs.openktree.com) | Developer documentation |
| **MCP Server** | [mcp.openktree.com](https://mcp.openktree.com) | Model Context Protocol endpoint |
| **API** | [api.openktree.com](https://api.openktree.com) | REST + SSE API |

## Getting Started

### Using the Research App

The fastest way to try Knowledge Tree is through the [Research App](https://research.openktree.com). Join the waitlist to get access, then:

1. **Ingest sources** — paste links or upload documents to feed the knowledge graph
2. **Explore the graph** — browse nodes, facts, dimensions, and relationships
3. **Create syntheses** — generate research documents that weave evidence into analytical narratives

### MCP Integration

Connect your AI tools (Claude Desktop, etc.) to the Knowledge Tree graph via the [Model Context Protocol](https://docs.openktree.com/mcp/overview). Browse nodes, facts, dimensions, and relationships directly from any MCP client.

See the [MCP docs](https://docs.openktree.com/mcp/connecting) for setup instructions.

### Local Development

Knowledge Tree is a microservices monorepo using **uv** (Python) and **pnpm** (frontend). To run locally:

```bash
# Prerequisites: Docker, uv, pnpm, just

# Start infrastructure (Postgres, Redis, Hatchet)
just setup

# Run database migrations
just migrate

# Start the API, workers, and frontend
just api-dev          # Terminal 1 — FastAPI on port 8000
just worker           # Terminal 2 — Hatchet workers
cd frontend && pnpm dev  # Terminal 3 — Next.js frontend
```

See the [development setup guide](https://docs.openktree.com/contributing/development-setup) for full instructions.

## Documentation

- [How It Works](https://docs.openktree.com/how-it-works/values-and-principles) — Core concepts: facts, entities, seeds, nodes, dimensions, and synthesis
- [MCP Integration](https://docs.openktree.com/mcp/overview) — Connect AI tools to the knowledge graph
- [Contributing](https://docs.openktree.com/contributing/architecture-overview) — Architecture, core objects, services, and development setup

## License

This project is licensed under AGPL-3.0. See [LICENSE](LICENSE) for details.
