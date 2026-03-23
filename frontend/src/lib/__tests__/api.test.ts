import { describe, it, expect, vi, beforeEach } from "vitest";
import { api } from "@/lib/api";

// ---------------------------------------------------------------------------
// Fetch mock
// ---------------------------------------------------------------------------

const mockFetch = vi.fn();
global.fetch = mockFetch;

function mockOkResponse(body: unknown) {
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  };
}

function mockErrorResponse(status: number, statusText: string, body: string) {
  return {
    ok: false,
    status,
    statusText,
    json: () => Promise.reject(new Error("not json")),
    text: () => Promise.resolve(body),
  };
}

beforeEach(() => {
  mockFetch.mockReset();
});

const BASE = "http://localhost:8000/api/v1";

// ---------------------------------------------------------------------------
// conversations
// ---------------------------------------------------------------------------

describe("api.conversations", () => {
  it("create sends POST with correct method, headers, and body", async () => {
    const responseBody = { id: "c1", title: "What is gravity?", messages: [] };
    mockFetch.mockResolvedValueOnce(mockOkResponse(responseBody));

    const result = await api.conversations.create({
      message: "What is gravity?",
      nav_budget: 50,
      explore_budget: 10,
    });

    expect(mockFetch).toHaveBeenCalledOnce();
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toBe(`${BASE}/conversations`);
    expect(options.method).toBe("POST");
    expect(options.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(options.body)).toEqual({
      message: "What is gravity?",
      nav_budget: 50,
      explore_budget: 10,
    });
    expect(result).toEqual(responseBody);
  });

  it("get sends GET to correct URL", async () => {
    const responseBody = { id: "abc-123", title: "test", messages: [] };
    mockFetch.mockResolvedValueOnce(mockOkResponse(responseBody));

    const result = await api.conversations.get("abc-123");

    expect(mockFetch).toHaveBeenCalledOnce();
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toBe(`${BASE}/conversations/abc-123`);
    expect(options.method).toBeUndefined();
    expect(result).toEqual(responseBody);
  });

  it("list sends GET with pagination params", async () => {
    const body = { items: [], total: 0, offset: 0, limit: 20 };
    mockFetch.mockResolvedValueOnce(mockOkResponse(body));

    const result = await api.conversations.list({ offset: 5, limit: 10 });

    const [url] = mockFetch.mock.calls[0];
    expect(url).toContain(`${BASE}/conversations?`);
    expect(url).toContain("offset=5");
    expect(url).toContain("limit=10");
    expect(result).toEqual(body);
  });

  it("sendMessage sends POST with message body", async () => {
    const responseBody = { id: "m1", turn_number: 2, role: "assistant" };
    mockFetch.mockResolvedValueOnce(mockOkResponse(responseBody));

    await api.conversations.sendMessage("conv-1", {
      message: "Tell me more",
      nav_budget: 10,
      explore_budget: 2,
    });

    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toBe(`${BASE}/conversations/conv-1/messages`);
    expect(options.method).toBe("POST");
    expect(JSON.parse(options.body)).toEqual({
      message: "Tell me more",
      nav_budget: 10,
      explore_budget: 2,
    });
  });
});

// ---------------------------------------------------------------------------
// nodes
// ---------------------------------------------------------------------------

describe("api.nodes", () => {
  it("search builds query string with query param", async () => {
    mockFetch.mockResolvedValueOnce(mockOkResponse([]));

    await api.nodes.search("photosynthesis");

    const [url] = mockFetch.mock.calls[0];
    expect(url).toBe(`${BASE}/nodes/search?query=photosynthesis`);
  });

  it("search builds query string with query and limit", async () => {
    mockFetch.mockResolvedValueOnce(mockOkResponse([]));

    await api.nodes.search("photosynthesis", 5);

    const [url] = mockFetch.mock.calls[0];
    expect(url).toContain("query=photosynthesis");
    expect(url).toContain("limit=5");
  });

  it("search omits limit when not provided", async () => {
    mockFetch.mockResolvedValueOnce(mockOkResponse([]));

    await api.nodes.search("test");

    const [url] = mockFetch.mock.calls[0];
    expect(url).not.toContain("limit");
  });

  it("get encodes node ID in URL", async () => {
    mockFetch.mockResolvedValueOnce(mockOkResponse({ id: "id/with/slashes" }));

    await api.nodes.get("id/with/slashes");

    const [url] = mockFetch.mock.calls[0];
    expect(url).toBe(`${BASE}/nodes/${encodeURIComponent("id/with/slashes")}`);
  });
});

// ---------------------------------------------------------------------------
// Error handling
// ---------------------------------------------------------------------------

describe("api error handling", () => {
  it("throws an error with status info for non-ok responses", async () => {
    mockFetch.mockResolvedValueOnce(
      mockErrorResponse(404, "Not Found", "node not found"),
    );

    await expect(api.nodes.get("missing")).rejects.toThrow(
      /API error 404 Not Found/,
    );
  });

  it("includes response body in error message", async () => {
    mockFetch.mockResolvedValueOnce(
      mockErrorResponse(500, "Internal Server Error", "unexpected failure"),
    );

    await expect(api.conversations.get("bad")).rejects.toThrow(/unexpected failure/);
  });

  it("handles text() rejection gracefully", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 502,
      statusText: "Bad Gateway",
      text: () => Promise.reject(new Error("stream error")),
    });

    await expect(api.graph.getStats()).rejects.toThrow(
      /API error 502 Bad Gateway/,
    );
  });
});

// ---------------------------------------------------------------------------
// graph.getPaths
// ---------------------------------------------------------------------------

describe("api.graph.getPaths", () => {
  it("builds correct URL with source and target params", async () => {
    const body = { source_id: "s1", target_id: "t1", paths: [], total_found: 0, max_depth: 6, truncated: false };
    mockFetch.mockResolvedValueOnce(mockOkResponse(body));

    const result = await api.graph.getPaths("s1", "t1");

    const [url] = mockFetch.mock.calls[0];
    expect(url).toContain(`${BASE}/graph/paths?`);
    expect(url).toContain("source=s1");
    expect(url).toContain("target=t1");
    expect(url).not.toContain("max_depth");
    expect(url).not.toContain("limit");
    expect(result).toEqual(body);
  });

  it("includes max_depth and limit when provided", async () => {
    mockFetch.mockResolvedValueOnce(mockOkResponse({ paths: [] }));

    await api.graph.getPaths("s1", "t1", 4, 3);

    const [url] = mockFetch.mock.calls[0];
    expect(url).toContain("max_depth=4");
    expect(url).toContain("limit=3");
  });

  it("omits optional params when not provided", async () => {
    mockFetch.mockResolvedValueOnce(mockOkResponse({ paths: [] }));

    await api.graph.getPaths("s1", "t1");

    const [url] = mockFetch.mock.calls[0];
    expect(url).not.toContain("max_depth");
    expect(url).not.toContain("limit");
  });
});

// ---------------------------------------------------------------------------
// buildQuery (tested indirectly via api methods)
// ---------------------------------------------------------------------------

describe("buildQuery behavior (via api calls)", () => {
  it("produces no query string when all params are undefined", async () => {
    mockFetch.mockResolvedValueOnce(mockOkResponse([]));

    await api.nodes.getEdges("node-1");

    const [url] = mockFetch.mock.calls[0];
    // direction is undefined so no query string
    expect(url).toBe(`${BASE}/nodes/node-1/edges`);
  });

  it("includes only defined params", async () => {
    mockFetch.mockResolvedValueOnce(mockOkResponse([]));

    await api.nodes.getEdges("node-1", "outbound");

    const [url] = mockFetch.mock.calls[0];
    expect(url).toBe(`${BASE}/nodes/node-1/edges?direction=outbound`);
  });

  it("includes all defined params for search", async () => {
    mockFetch.mockResolvedValueOnce(mockOkResponse([]));

    await api.facts.search("claim");

    const [url] = mockFetch.mock.calls[0];
    expect(url).toBe(`${BASE}/facts/search?fact_type=claim`);
  });
});

// ---------------------------------------------------------------------------
// Node management (list, update, delete)
// ---------------------------------------------------------------------------

describe("api.nodes management", () => {
  it("list sends GET with pagination params", async () => {
    const body = { items: [], total: 0, offset: 0, limit: 20 };
    mockFetch.mockResolvedValueOnce(mockOkResponse(body));

    const result = await api.nodes.list({ offset: 10, limit: 5, search: "test" });

    const [url] = mockFetch.mock.calls[0];
    expect(url).toContain(`${BASE}/nodes?`);
    expect(url).toContain("offset=10");
    expect(url).toContain("limit=5");
    expect(url).toContain("search=test");
    expect(result).toEqual(body);
  });

  it("list sends GET with no params when none provided", async () => {
    mockFetch.mockResolvedValueOnce(mockOkResponse({ items: [], total: 0, offset: 0, limit: 20 }));

    await api.nodes.list();

    const [url] = mockFetch.mock.calls[0];
    expect(url).toBe(`${BASE}/nodes`);
  });

  it("update sends PATCH with body", async () => {
    const updated = { id: "n1", concept: "updated" };
    mockFetch.mockResolvedValueOnce(mockOkResponse(updated));

    await api.nodes.update("n1", { concept: "updated" });

    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toBe(`${BASE}/nodes/n1`);
    expect(options.method).toBe("PATCH");
    expect(JSON.parse(options.body)).toEqual({ concept: "updated" });
  });

  it("delete sends DELETE request", async () => {
    mockFetch.mockResolvedValueOnce(mockOkResponse({ deleted: true, id: "n1" }));

    const result = await api.nodes.delete("n1");

    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toBe(`${BASE}/nodes/n1`);
    expect(options.method).toBe("DELETE");
    expect(result.deleted).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Fact management (list, update, delete)
// ---------------------------------------------------------------------------

describe("api.facts management", () => {
  it("list sends GET with params", async () => {
    const body = { items: [], total: 0, offset: 0, limit: 20 };
    mockFetch.mockResolvedValueOnce(mockOkResponse(body));

    await api.facts.list({ offset: 0, limit: 10, search: "water", fact_type: "claim" });

    const [url] = mockFetch.mock.calls[0];
    expect(url).toContain(`${BASE}/facts?`);
    expect(url).toContain("limit=10");
    expect(url).toContain("search=water");
    expect(url).toContain("fact_type=claim");
  });

  it("update sends PATCH with body", async () => {
    mockFetch.mockResolvedValueOnce(mockOkResponse({ id: "f1", content: "new" }));

    await api.facts.update("f1", { content: "new" });

    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toBe(`${BASE}/facts/f1`);
    expect(options.method).toBe("PATCH");
    expect(JSON.parse(options.body)).toEqual({ content: "new" });
  });

  it("delete sends DELETE request", async () => {
    mockFetch.mockResolvedValueOnce(mockOkResponse({ deleted: true, id: "f1" }));

    const result = await api.facts.delete("f1");

    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toBe(`${BASE}/facts/f1`);
    expect(options.method).toBe("DELETE");
    expect(result.deleted).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------

describe("api.export", () => {
  it("nodes sends GET to /export/nodes", async () => {
    const body = { metadata: {}, nodes: [] };
    mockFetch.mockResolvedValueOnce(mockOkResponse(body));

    const result = await api.export.nodes();

    const [url] = mockFetch.mock.calls[0];
    expect(url).toBe(`${BASE}/export/nodes`);
    expect(result).toEqual(body);
  });

  it("facts sends GET to /export/facts", async () => {
    const body = { metadata: {}, facts: [] };
    mockFetch.mockResolvedValueOnce(mockOkResponse(body));

    const result = await api.export.facts();

    const [url] = mockFetch.mock.calls[0];
    expect(url).toBe(`${BASE}/export/facts`);
    expect(result).toEqual(body);
  });

  it("conversation sends GET to /export/conversations/:id", async () => {
    const body = { metadata: {}, conversation: {}, nodes: [], edges: [], facts: [] };
    mockFetch.mockResolvedValueOnce(mockOkResponse(body));

    const result = await api.export.conversation("conv-42");

    const [url] = mockFetch.mock.calls[0];
    expect(url).toBe(`${BASE}/export/conversations/conv-42`);
    expect(result).toEqual(body);
  });

  it("conversation encodes special characters in ID", async () => {
    mockFetch.mockResolvedValueOnce(mockOkResponse({}));

    await api.export.conversation("id/with/slashes");

    const [url] = mockFetch.mock.calls[0];
    expect(url).toBe(`${BASE}/export/conversations/${encodeURIComponent("id/with/slashes")}`);
  });
});

// ---------------------------------------------------------------------------
// Import
// ---------------------------------------------------------------------------

describe("api.import", () => {
  it("facts sends POST to /import/facts", async () => {
    const body = { imported_facts: [], imported_nodes: [], imported_edges: 0, imported_sources: 0, errors: [] };
    mockFetch.mockResolvedValueOnce(mockOkResponse(body));

    const result = await api.import.facts({ facts: [] });

    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toBe(`${BASE}/import/facts`);
    expect(options.method).toBe("POST");
    expect(JSON.parse(options.body)).toEqual({ facts: [] });
    expect(result).toEqual(body);
  });

  it("nodes sends POST to /import/nodes", async () => {
    const body = { imported_facts: [], imported_nodes: [], imported_edges: 0, imported_sources: 0, errors: [] };
    mockFetch.mockResolvedValueOnce(mockOkResponse(body));

    const result = await api.import.nodes({ nodes: [] });

    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toBe(`${BASE}/import/nodes`);
    expect(options.method).toBe("POST");
    expect(JSON.parse(options.body)).toEqual({ nodes: [] });
    expect(result).toEqual(body);
  });

  it("factsStream sends POST to /import/facts/stream and parses SSE events", async () => {
    const completeResult = { imported_facts: [], imported_nodes: [], imported_edges: 0, imported_sources: 0, errors: [] };
    const sseData = [
      'data: {"type":"start","phase":"facts","total":0}\n\n',
      `data: {"type":"complete","result":${JSON.stringify(completeResult)}}\n\n`,
    ].join("");

    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(sseData));
        controller.close();
      },
    });

    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      statusText: "OK",
      body: stream,
    });

    const progressCalls: Array<{ phase: string; processed: number; total: number }> = [];
    const result = await api.import.factsStream(
      { facts: [] },
      (p) => progressCalls.push(p),
    );

    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toBe(`${BASE}/import/facts/stream`);
    expect(options.method).toBe("POST");
    expect(result).toEqual(completeResult);
  });

  it("nodesStream sends POST to /import/nodes/stream and reports progress", async () => {
    const completeResult = { imported_facts: [], imported_nodes: [], imported_edges: 0, imported_sources: 0, errors: [] };
    const sseData = [
      'data: {"type":"start","facts":0,"nodes":1,"links":0,"edges":0}\n\n',
      'data: {"type":"progress","phase":"nodes","processed":1,"total":1}\n\n',
      `data: {"type":"complete","result":${JSON.stringify(completeResult)}}\n\n`,
    ].join("");

    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(sseData));
        controller.close();
      },
    });

    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      statusText: "OK",
      body: stream,
    });

    const progressCalls: Array<{ phase: string; processed: number; total: number }> = [];
    const result = await api.import.nodesStream(
      { nodes: [] },
      (p) => progressCalls.push(p),
    );

    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toBe(`${BASE}/import/nodes/stream`);
    expect(options.method).toBe("POST");
    expect(result).toEqual(completeResult);
    expect(progressCalls).toEqual([{ phase: "nodes", processed: 1, total: 1 }]);
  });
});
