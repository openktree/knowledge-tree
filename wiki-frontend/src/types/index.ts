export interface NodeResponse {
  id: string;
  concept: string;
  node_type: string;
  key: string;
  parent_id: string | null;
  parent_concept: string | null;
  parent_key: string | null;
  definition: string | null;
  definition_source: string | null;
  edge_count: number;
  child_count: number;
  richness: number;
  metadata?: Record<string, unknown> | null;
}

export interface EdgeResponse {
  id: string;
  source_node_id: string;
  source_node_concept: string | null;
  target_node_id: string;
  target_node_concept: string | null;
  relationship_type: string;
  weight: number;
  justification: string | null;
  supporting_fact_ids: string[];
}

export interface EdgeDetailResponse extends EdgeResponse {
  supporting_facts: FactResponse[];
  created_at: string;
}

export interface FactSourceInfo {
  source_id: string;
  uri: string;
  title: string | null;
  provider_id: string;
  published_date: string | null;
  author_person: string | null;
  author_org: string | null;
}

export interface FactResponse {
  id: string;
  content: string;
  fact_type: string;
  stance: string | null;
  sources: FactSourceInfo[];
}

export interface DimensionResponse {
  id: string;
  node_id: string;
  model_id: string;
  content: string;
  confidence: number;
  is_definitive: boolean;
}

export interface FactNodeInfo {
  node_id: string;
  concept: string;
  node_type: string;
}

export interface SubgraphResponse {
  nodes: NodeResponse[];
  edges: EdgeResponse[];
}
