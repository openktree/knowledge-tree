---
sidebar_position: 2
title: Connecting
---

# Connecting to the MCP Server

## Prerequisites

1. An API token generated from the Research App ([research.openktree.com](https://research.openktree.com)) under **Profile > API Tokens**
2. An MCP-compatible client (Claude Desktop, Claude Code, or another MCP client)

## Claude Desktop

Add the following to your Claude Desktop configuration file:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "knowledge-tree": {
      "url": "https://mcp.openktree.com/sse",
      "headers": {
        "Authorization": "Bearer YOUR_API_TOKEN"
      }
    }
  }
}
```

Replace `YOUR_API_TOKEN` with the token from your profile page.

## Claude Code

Add to your Claude Code settings or project `.mcp.json`:

```json
{
  "mcpServers": {
    "knowledge-tree": {
      "url": "https://mcp.openktree.com/sse",
      "headers": {
        "Authorization": "Bearer YOUR_API_TOKEN"
      }
    }
  }
}
```

## Local development

If you're running the Knowledge Tree stack locally, the MCP server runs on port 8001:

```json
{
  "mcpServers": {
    "knowledge-tree-local": {
      "url": "http://localhost:8001/sse",
      "headers": {
        "Authorization": "Bearer YOUR_LOCAL_TOKEN"
      }
    }
  }
}
```

You can bypass authentication in development by setting `SKIP_AUTH=true` in the MCP server's environment.

## Other MCP clients

Any client that supports the MCP SSE transport can connect. The server URL is:

```
https://mcp.openktree.com/sse
```

Pass the API token as a Bearer token in the `Authorization` header.

## Verifying the connection

Once connected, try searching for a topic:

> "Use the Knowledge Tree to search for 'quantum computing'"

Your AI client should call `search_graph` and return a list of matching nodes from the graph.
