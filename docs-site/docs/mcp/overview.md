---
sidebar_position: 1
title: Overview
---

# MCP Integration Overview

Knowledge Tree provides a **Model Context Protocol (MCP)** server that gives any MCP-compatible AI client read-only access to the knowledge graph. This means you can explore nodes, facts, dimensions, and relationships directly from tools like Claude Desktop.

## What is MCP?

The [Model Context Protocol](https://modelcontextprotocol.io/) is an open standard that lets AI assistants connect to external data sources and tools. Instead of copying data into prompts, MCP provides structured, on-demand access to live data.

## What the Knowledge Tree MCP server provides

The MCP server exposes **8 tools** for navigating the knowledge graph:

| Tool | Purpose |
|------|---------|
| [`search_graph`](/mcp/available-tools#search_graph) | Find nodes by text search |
| [`get_node`](/mcp/available-tools#get_node) | Load node details, definition, counts |
| [`get_dimensions`](/mcp/available-tools#get_dimensions) | Load multi-model analyses (paginated) |
| [`get_edges`](/mcp/available-tools#get_edges) | Load relationships, sorted by evidence strength |
| [`get_facts`](/mcp/available-tools#get_facts) | Load facts grouped by source, with powerful filtering |
| [`get_fact_sources`](/mcp/available-tools#get_fact_sources) | Load deduplicated source list for provenance |
| [`search_facts`](/mcp/available-tools#search_facts) | Search the global fact pool |
| [`get_node_paths`](/mcp/available-tools#get_node_paths) | Find shortest paths between two nodes |

All tools are **read-only** — they query the graph but never modify it.

## Authentication

The MCP server uses **Bearer token authentication**. You generate an API token from the Research App's profile page, then include it in your MCP client configuration.

## Getting started

1. [Connect your MCP client](/mcp/connecting)
2. [Explore the available tools](/mcp/available-tools)
3. [Try common workflows](/mcp/examples)
