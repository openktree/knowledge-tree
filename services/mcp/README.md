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

## Authentication

The MCP server supports two authentication methods. OAuth 2.1 is the primary protocol; API tokens are available as a fallback for simpler clients.

### OAuth 2.1 with PKCE (primary)

The server implements a full OAuth 2.1 authorization flow with PKCE, designed for interactive clients like Claude Desktop and Claude Web. The flow is automatic for clients that support MCP OAuth — they discover endpoints, register, and handle the token exchange without manual configuration.

**How it works:**

1. The client discovers OAuth endpoints via `/.well-known/oauth-authorization-server`
2. The client dynamically registers itself via `/register` (RFC 7591)
3. The client redirects the user to `/authorize` with a PKCE code challenge
4. The user authenticates at the login page (`/oauth/login`) with their email and password
5. An authorization code is returned to the client's redirect URI
6. The client exchanges the code + PKCE verifier for access and refresh tokens at `/token`
7. The client uses the access token as a Bearer token for all MCP requests

**Token lifetimes:**

| Token | Lifetime |
|---|---|
| Authorization code | 5 minutes (single-use) |
| Access token | 1 hour |
| Refresh token | 30 days |

Tokens are stored as SHA-256 hashes — plaintext is never persisted. Expired tokens are cleaned up automatically.

**Connecting Claude Code (OAuth):**

```bash
claude mcp add --transport http --scope user knowledge-tree http://127.0.0.1:8001/mcp
```

Claude Code handles the OAuth flow automatically. You'll be prompted to log in on first use.

### API tokens (fallback)

For non-interactive clients, scripts, or environments that don't support OAuth, you can authenticate with a static API token. Generate one from the Research App under **Profile > API Tokens** or via the API (`POST /api/v1/auth/tokens`).

```bash
claude mcp add --transport http --scope user knowledge-tree http://127.0.0.1:8001/mcp \
  --header "Authorization: Bearer <YOUR_API_TOKEN>"
```

Verify with `/mcp` inside Claude Code.

### Development

Auth is bypassed entirely when `SKIP_AUTH=true` is set in the environment.

### Configuration

Set `MCP_OAUTH_BASE_URL` to the public-facing URL of the MCP server (defaults to `http://localhost:8001`). This is used to construct OAuth redirect URLs.
