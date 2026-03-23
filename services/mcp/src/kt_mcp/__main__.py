"""MCP server entrypoint."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("kt_mcp.server:app", host="0.0.0.0", port=8001, reload=True)
