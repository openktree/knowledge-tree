---
sidebar_position: 2
title: Connecting
---

# Connecting to the MCP Server

## Prerequisites

1. A Knowledge Tree account at [research.openktree.com](https://research.openktree.com)
2. An MCP-compatible client (Claude Desktop, Claude Code, or another MCP client)

## OAuth 2.1 (recommended)

MCP clients that support OAuth handle authentication automatically. You only need to provide the server URL — the client discovers OAuth endpoints, registers itself, and prompts you to log in.

### Claude Desktop

Add the following to your Claude Desktop configuration file:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "knowledge-tree": {
      "url": "https://mcp.openktree.com/mcp"
    }
  }
}
```

On first use, Claude Desktop will open a browser window where you log in with your Knowledge Tree account. Tokens are refreshed automatically.

### Claude Code

Add to your Claude Code settings or project `.mcp.json`:

```json
{
  "mcpServers": {
    "knowledge-tree": {
      "type": "http",
      "url": "https://mcp.openktree.com/mcp"
    }
  }
}
```

Or via the CLI:

```bash
claude mcp add --transport http --scope user knowledge-tree https://mcp.openktree.com/mcp
```

### Other MCP clients

Any client that supports MCP OAuth can connect. Point it at:

```
https://mcp.openktree.com/mcp
```

The client will discover the OAuth configuration from `/.well-known/oauth-authorization-server` and handle the flow.

## API tokens (fallback)

For scripts, CI pipelines, or clients that don't support OAuth, you can authenticate with a static API token. Generate one from the Research App under **Profile > API Tokens** or via `POST /api/v1/auth/tokens`.

### Claude Desktop

```json
{
  "mcpServers": {
    "knowledge-tree": {
      "url": "https://mcp.openktree.com/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_API_TOKEN"
      }
    }
  }
}
```

### Claude Code

```json
{
  "mcpServers": {
    "knowledge-tree": {
      "type": "http",
      "url": "https://mcp.openktree.com/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_API_TOKEN"
      }
    }
  }
}
```

Replace `YOUR_API_TOKEN` with the token from your profile page.

## Local development

If you're running the Knowledge Tree stack locally, the MCP server runs on port 8001:

```json
{
  "mcpServers": {
    "knowledge-tree-local": {
      "type": "http",
      "url": "http://localhost:8001/mcp"
    }
  }
}
```

OAuth works the same way locally — you'll be prompted to log in at `http://localhost:8001/oauth/login`.

You can bypass authentication entirely in development by setting `SKIP_AUTH=true` in the MCP server's environment.

## Verifying the connection

Once connected, try searching for a topic:

> "Use the Knowledge Tree to search for 'quantum computing'"

Your AI client should call `search_graph` and return a list of matching nodes from the graph.
