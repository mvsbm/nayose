import type { EntityHit } from "./port.js";

export interface EntityExplorerPage {
  rows: EntityHit[];
  total: number;
  offset: number;
  limit: number;
  query: string;
  sort: "relevance" | "records" | "name" | "last_seen";
  filters: Record<string, unknown>;
  query_plan: Record<string, unknown>;
  search_mode: "exact" | "hybrid_fallback";
  total_is_exact: boolean;
  cursor?: string | null;
  next_cursor?: string | null;
  has_more?: boolean;
  search_trace: Record<string, unknown>;
}

export interface EntityGraphNode {
  id: string;
  kind: "entity" | "record" | "policy" | "agency" | "channel";
  label: string;
  metadata: Record<string, unknown>;
}

export interface EntityGraphEdge {
  source: string;
  target: string;
  kind: "observed" | "potential";
  relation: string;
  evidence: Record<string, unknown>;
}

export interface EntityGraph {
  entity_id: string;
  nodes: EntityGraphNode[];
  edges: EntityGraphEdge[];
  truncated: boolean;
  potential_matches_available: boolean;
}
