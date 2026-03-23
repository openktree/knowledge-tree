# Knowledge Tree MCP Server

Read-only access to the knowledge graph via [Model Context Protocol](https://modelcontextprotocol.io/).

## Tools

| Tool | Description |
|---|---|
| `search_graph` | Find nodes by text query, optional type filter |
| `get_node` | Load node definition, dimensions, and edges |
| `get_facts` | Load facts linked to a node |
| `get_fact_sources` | Load provenance sources for a node's facts |

## Running

```bash
just mcp-dev
```

The server starts on `http://127.0.0.1:8001` with the MCP endpoint at `/mcp`.

## Connecting Claude Code

```bash
claude mcp add --transport http --scope user knowledge-tree http://127.0.0.1:8001/mcp \
  --header "Authorization: Bearer <YOUR_API_TOKEN>"
```

Verify with `/mcp` inside Claude Code.

## Auth

All requests require a bearer token matching a valid entry in the `api_tokens` table. Create a token via the API (`POST /api/v1/auth/tokens`). Auth is bypassed when `SKIP_AUTH=true`.
