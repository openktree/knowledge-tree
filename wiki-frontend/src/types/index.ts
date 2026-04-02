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
  fact_count: number;
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

export interface SynthesisListItem {
  id: string;
  concept: string;
  node_type: string;
  visibility: string;
  model_id: string | null;
  sentence_count: number;
  sub_synthesis_ids: string[];
  created_at: string | null;
}

export interface SynthesisSentenceResponse {
  position: number;
  text: string;
  fact_count: number;
  node_ids: string[];
}

export interface SynthesisNodeResponse {
  node_id: string;
  concept: string;
  node_type: string;
}

export interface SynthesisDocumentResponse {
  id: string;
  concept: string;
  node_type: string;
  visibility: string;
  definition: string | null;
  model_id: string | null;
  sentences: SynthesisSentenceResponse[];
  referenced_nodes: SynthesisNodeResponse[];
  sub_syntheses: SynthesisNodeResponse[];
  created_at: string | null;
}

export interface SentenceFactResponse {
  fact_id: string;
  content: string;
  fact_type: string;
  embedding_distance: number;
  source_title: string;
  source_uri: string;
  author: string;
}
