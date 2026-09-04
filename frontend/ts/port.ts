// Ports (hexagonal): the frontend depends on these interfaces, never on
// fetch/SQL/localStorage directly. Swap WASM vs API by providing a different
// QueryPort at the composition root.

export interface EntityHit {
  nayose_id: string;
  name_kanji: string;
  name_kana?: string;
  phone?: string;
  postal_code?: string;
  record_count?: number;
}

export interface EntityDetail {
  entity: Record<string, unknown>;
  members: Record<string, unknown>[];
  lineage: Record<string, string[]>;
}

export interface ReviewRow {
  left_record_id: string;
  right_record_id: string;
  pair_probability: number;
  exact_count?: number;
}

export interface ReviewPage {
  total: number;
  rows: ReviewRow[];
}

export interface RecommendResult {
  action: string;
  top_nayose_id: string | null;
  top_probability: number;
  margin: number;
  candidates: Array<Record<string, unknown>>;
}

export interface ConfusionData {
  pairs: Record<string, number>;
  scenarios: Array<{ scenario: string; actions: Record<string, number> }>;
  decisions: Record<string, Array<Record<string, unknown>>>;
}

export interface MetricsViewData {
  kpis: Array<[string, number]>;
  extras: Record<string, number>;
}

export interface QueryPort {
  run(id: string): void;
  entities(q: string): Promise<EntityHit[]>;
  entity(id: string): Promise<EntityDetail>;
  review(minP: number, offset: number, limit: number): Promise<ReviewPage>;
  metrics(): Promise<MetricsViewData>;
  confusion(): Promise<ConfusionData>;
  compare(l: string, r: string): Promise<Record<string, unknown>[]>;
  recommend(record: Record<string, string>): Promise<RecommendResult>;
}

export interface Storage {
  read(key: string): string | null;
  write(key: string, value: string): void;
}

export interface StoreEvents {
  onRun(fn: () => void): void;
  onTheme(fn: () => void): void;
  onChecked(fn: () => void): void;
}