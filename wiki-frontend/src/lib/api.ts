import type {
  NodeResponse,
  SubgraphResponse,
  EdgeDetailResponse,
  FactResponse,
  DimensionResponse,
  FactNodeInfo,
  SynthesisListItem,
  SynthesisDocumentResponse,
  SentenceFactResponse,
} from "../types/index.js";

const API_BASE_URL =
  process.env.API_BASE_URL ?? import.meta.env.API_BASE_URL ?? "http://localhost:8000";
const API_TOKEN: string | undefined =
  process.env.API_TOKEN ?? import.meta.env.API_TOKEN;

function authHeaders(): HeadersInit {
  if (API_TOKEN) {
    return { Authorization: `Bearer ${API_TOKEN}` };
  }
  return {};
}

async function get<T>(path: string): Promise<T> {
  const url = `${API_BASE_URL}${path}`;
  const res = await fetch(url, { headers: authHeaders() });
  if (!res.ok) {
    if (res.status === 401) {
      throw new Error(
        "Backend returned 401 Unauthorized. Set API_TOKEN env var or run backend with SKIP_AUTH=true."
      );
    }
    throw new Error(`API error ${res.status} ${res.statusText} for ${path}`);
  }
  return res.json() as Promise<T>;
}

/**
 * Generate a URL-friendly node key from type and concept.
 * Mirrors the backend's `make_url_key()` logic.
 */
export function makeNodeUrlKey(nodeType: string, concept: string): string {
  const slug = concept
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
  return `${nodeType}-${slug}`;
}

/**
 * Return the URL path for a node. Prefers `key` if available, falls back to computing from type+concept.
 */
export function nodeHref(node: { key?: string; id: string; node_type: string; concept: string }): string {
  const key = node.key || makeNodeUrlKey(node.node_type, node.concept);
  return `/nodes/${key}`;
}

export async function getNode(id: string): Promise<NodeResponse> {
  return get<NodeResponse>(`/api/v1/nodes/${id}`);
}

export async function getSubgraph(id: string): Promise<SubgraphResponse> {
  return get<SubgraphResponse>(
    `/api/v1/graph/subgraph?node_ids=${id}&depth=1`
  );
}

export async function getNodeFacts(id: string): Promise<FactResponse[]> {
  return get<FactResponse[]>(`/api/v1/nodes/${id}/facts?limit=100`);
}

export async function getNodeDimensions(
  id: string
): Promise<DimensionResponse[]> {
  return get<DimensionResponse[]>(`/api/v1/nodes/${id}/dimensions`);
}

export async function getNodeChildren(id: string): Promise<NodeResponse[]> {
  return get<NodeResponse[]>(`/api/v1/nodes/${id}/children`);
}

export async function listNodes(limit = 100, sort = "updated_at"): Promise<NodeResponse[]> {
  const data = await get<{ items: NodeResponse[]; total: number }>(
    `/api/v1/nodes?limit=${limit}&sort=${sort}`
  );
  return data.items;
}

export async function getFact(id: string): Promise<FactResponse> {
  return get<FactResponse>(`/api/v1/facts/${id}`);
}

export async function getFactNodes(id: string): Promise<FactNodeInfo[]> {
  return get<FactNodeInfo[]>(`/api/v1/facts/${id}/nodes`);
}

export async function getEdge(id: string): Promise<EdgeDetailResponse> {
  return get<EdgeDetailResponse>(`/api/v1/edges/${id}`);
}

export async function getEdgesBetween(
  nodeIdA: string,
  nodeIdB: string,
): Promise<EdgeDetailResponse[]> {
  // Use depth=1 like the node page does — guaranteed to find all edges
  const subgraph = await get<SubgraphResponse>(
    `/api/v1/graph/subgraph?node_ids=${nodeIdA}&depth=1`,
  );
  const matching = subgraph.edges.filter(
    (e) =>
      (e.source_node_id === nodeIdA && e.target_node_id === nodeIdB) ||
      (e.source_node_id === nodeIdB && e.target_node_id === nodeIdA),
  );
  // Fetch full details (with facts) for each edge
  return Promise.all(matching.map((e) => getEdge(e.id)));
}

export async function searchNodes(
  q: string,
  limit = 50
): Promise<NodeResponse[]> {
  const encoded = encodeURIComponent(q);
  return get<NodeResponse[]>(
    `/api/v1/nodes/search?query=${encoded}&limit=${limit}`
  );
}

export async function listSyntheses(
  limit = 50,
  visibility = "public"
): Promise<SynthesisListItem[]> {
  const data = await get<{ items: SynthesisListItem[]; total: number }>(
    `/api/v1/syntheses?limit=${limit}&visibility=${visibility}`
  );
  return data.items;
}

export async function getSynthesis(id: string): Promise<SynthesisDocumentResponse> {
  return get<SynthesisDocumentResponse>(`/api/v1/syntheses/${id}`);
}

export async function getSentenceFacts(
  synthesisId: string,
  position: number
): Promise<SentenceFactResponse[]> {
  return get<SentenceFactResponse[]>(
    `/api/v1/syntheses/${synthesisId}/sentences/${position}/facts`
  );
}

export function synthesisHref(synthesis: { id: string }): string {
  return `/syntheses/${synthesis.id}`;
}
