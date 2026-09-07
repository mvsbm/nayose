// Ports (hexagonal): the frontend depends on these interfaces, never on
// fetch/SQL/localStorage directly. Swap WASM vs API by providing a different
// QueryPort at the composition root.

export interface EntityHit {
  kind?: "exact" | "semantic" | "nearest";
  signals?: string[];
  match_field?: string;
  match_method?: string;
  search_backend?: string;
  twin_count?: number;
  prefecture?: string;
  party_type?: string;
  nayose_id: string;
  name_kanji: string;
  name_kana?: string;
  name_romaji?: string;
  date_birth?: string;
  address?: string;
  email?: string;
  phone?: string;
  postal_code?: string;
  record_count?: number;
  score?: number;
  retrieval_sources?: string[];
  retrieval_evidence?: Array<{ source: string; field?: string; score?: number | null }>;
}

export interface EntityDetail {
  entity: Record<string, unknown>;
  members: Record<string, unknown>[];
  total: number;
  offset: number;
  limit: number;
  comparisons: Record<string, Record<string, FieldComparison>>;
  merge_evidence_available: boolean;
}

export interface FieldComparison {
  left: string;
  right: string;
  left_normalized: string;
  right_normalized: string;
  status: "exact" | "normalized" | "different" | "missing";
}

export interface CandidateEvidence {
  record: Record<string, string>;
  score: number;
  comparisons: Record<string, FieldComparison>;
  features: Record<string, number>;
  retrieval?: Record<string, unknown>;
}

export interface PipelineNode {
  id: string;
  kind: string;
  status: "complete" | "warning" | "skipped";
  metrics: Record<string, unknown>;
  details: Record<string, unknown>;
}

export interface DecisionTrace {
  selected_candidate: string | null;
  nodes: PipelineNode[];
  edges: string[][];
}

export interface MatchCandidate {
  rank: number;
  nayose_id: string;
  name: string;
  probability: number;
  support: number;
  evidence: CandidateEvidence[];
}

export interface JobProgress {
  phase?: string;
  pct?: number | null;
  eta_seconds?: number | null;
  done?: number;
  total?: number;
}

export interface MatchJob {
  id: string;
  run_id: string;
  stage: "queued" | "validating" | "loading" | "retrieving" | "scoring" | "aggregating" | "complete" | "failed";
  elapsed_ms: number;
  timeline: Array<{ stage: string; elapsed_ms: number }>;
  progress?: JobProgress | null;
  error: string | null;
  result: RecommendResult | null;
}

export interface ReviewRow {
  left_record_id: string;
  right_record_id: string;
  pair_probability: number;
  exact_count?: number;
}

export interface SearchFilters {
  party_type?: "person" | "company";
  min_record_count?: number;
}

export interface SearchResult {
  rows: EntityHit[];
  exact?: EntityHit[];
  semantic_results?: EntityHit[];
  nearest?: EntityHit[];
  semantic: boolean;
  mode?: "browse" | "hybrid" | "exact_plus_fuzzy";
  embedding_backend?: string | null;
  query?: string;
  normalized_query?: Record<string, string>;
  query_plan?: Record<string, unknown>;
  search_trace?: Record<string, unknown>;
  suggestion?: { term: string; score: number; method?: string; field?: string; nayose_id?: string } | null;
}

export interface ReviewPage {
  total: number;
  rows: ReviewRow[];
  histogram: Array<[number, number]>;
  pair_auto_threshold?: number | null;
}

export interface HealthInfo {
  status: string;
  run?: string | null;
  fingerprint?: string;
  engine_warm?: boolean;
  now_utc?: string;
  stats?: Array<{ endpoint: string; hits: number; total_ms: number }>;
}

export interface RecommendResult {
  action: string;
  top_nayose_id: string | null;
  top_probability: number;
  margin: number;
  candidates: MatchCandidate[];
  reason: string;
  thresholds: { auto: number; review: number } | null;
  threshold_source?: string;
  nearest?: MatchCandidate[];
  input_normalized?: Record<string, string>;
  score_context?: { at_or_above: number; total: number; pct: number } | null;
  diagnostics: { retrieved?: number; entities?: number; reranker?: boolean; retrieval_backend?: string; exact_mode?: string };
  scoring_mode: string;
  pipeline?: DecisionTrace | null;
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

export interface RunInfo {
  id: string;
  path: string;
  current?: boolean;
  created?: string;
  fingerprint?: string;
}

export interface DatasetGeneration {
  id: string;
  current: boolean;
  created_at: string;
  fingerprint: string;
  snapshot_date: string;
  query_date: string;
  counts: Record<string, number>;
}

export interface DatasetSummary {
  id: string;
  current: boolean;
  created_at: string;
  fingerprint: string;
  config: Record<string, unknown>;
  counts: Record<string, number>;
  tables: Array<{ id: string; label: string; rows: number; columns: string[] }>;
}

export interface DatasetRows {
  table: string;
  columns: string[];
  rows: Array<Record<string, unknown>>;
  total: number;
  offset: number;
  limit: number;
}

export interface AdminAuditRow {
  ts: string;
  actor: string;
  action: string;
  [key: string]: unknown;
}

export interface AdminCapability {
  capable: boolean;
}

export interface GenerationControls {
  profile: string;
  difficulty: string;
  seed: number;
  n_parties: number;
  mean_records: number | null;
  n_agencies: number;
  person_share: number;
  query_share: number;
  twin_rate: number;
  poor: number | null;
  catastrophic: number | null;
  snapshot_date: string;
  query_date: string;
  event_rates: Record<string, number>;
  band_scale: Record<string, number>;
  missing_rates: Record<string, number>;
  field_availability: Record<string, Record<string, number>>;
  stale_rate: number | null;
  query_mix: Record<string, number>;
}

export interface GenerationCatalogItem {
  id: string;
  label_ja: string;
  label_en: string;
  description_ja: string;
  description_en: string;
}

export interface GenerationCatalog {
  profiles: Array<GenerationCatalogItem & { n_parties: number; mean_records: number }>;
  difficulties: Array<GenerationCatalogItem & { poor: number; catastrophic: number; quality_share: Record<string, number> }>;
  events: Array<GenerationCatalogItem & { group: string; fields: string[]; example_ja: string; example_en: string; default_rate: number }>;
  channels: Array<{ id: string; availability: Record<string, number>; bundle: string[] }>;
  query_scenarios: Array<GenerationCatalogItem & { default_share: number }>;
  defaults: GenerationControls;
  bounds: Record<string, Record<string, number>>;
}

export interface GenerationSample {
  quality: string;
  channel: string;
  canonical: Record<string, string>;
  observed: Record<string, string>;
  changes: Array<{ field: string; before: string; after: string; transformations: string[] }>;
}

export interface GenerationImpactQuality {
  id: string;
  share: number;
  records: number;
  corruption_scale: number;
  residual_missing_rate: number;
  estimated_missing_records: number;
}

export interface GenerationImpactChannel {
  id: string;
  share: number;
  records: number;
  bundle: string[];
  availability: Record<string, number>;
}

export interface GenerationDatasetImpact {
  quality: GenerationImpactQuality[];
  channels: GenerationImpactChannel[];
  fields: Array<{ id: string; estimated_missing_rate: number; estimated_missing_records: number }>;
  overall: { weighted_corruption_scale: number; weighted_residual_missing_rate: number; field_rate_note: string };
}

export interface GenerationPreview {
  config_hash: string;
  controls: GenerationControls;
  time_window: { snapshot_date: string; query_date: string; gap_days: number };
  dataset_impact: GenerationDatasetImpact;
  counts: { parties: number; records: number; queries: number; agencies: number };
  quality: Record<string, number>;
  weighted_band_scale: number;
  events: Array<GenerationCatalogItem & { group: string; fields: string[]; example_ja: string; example_en: string; rate: number; effective_rate: number; estimated_records: number }>;
  missingness: { by_quality: Record<string, number>; estimated_average_rate: number };
  channels: Array<{ id: string; availability: Record<string, number>; bundle: string[] }>;
  query_mix: Array<GenerationCatalogItem & { share: number; estimated_queries: number }>;
  samples: Record<string, GenerationSample>;
  examples: GenerationSample[];
  warnings: Array<{ key: string; ja: string; en: string }>;
  estimate_note: { ja: string; en: string };
}

export interface AdminState {
  profile: {
    profiles: string[];
    difficulties: string[];
    model_families: string[];
    devices: string[];
    exact_modes: string[];
    threads_default: number;
  };
  settings: {
    exact_mode: string;
    warm_embed: boolean;
    engine_warm: boolean;
    engine_backend?: string;
  };
  runs: Array<{ id: string; manifest: boolean; current: boolean }>;
  manual_count: number;
  audit: AdminAuditRow[];
}

export interface ManualList {
  count: number;
  rows: Array<Record<string, unknown>>;
}

export interface QueryPort {
  run(id: string): void;
  runs(): Promise<RunInfo[]>;
  adminCapability(): Promise<AdminCapability>;
  adminState(): Promise<AdminState>;
  adminGenerationConfig(): Promise<GenerationCatalog>;
  adminGenerationPreview(controls: GenerationControls): Promise<GenerationPreview>;
  adminGenerations(): Promise<DatasetGeneration[]>;
  adminDatasetSummary(generationId: string): Promise<DatasetSummary>;
  adminDatasetRows(generationId: string, table: string, query: string, offset: number, limit: number): Promise<DatasetRows>;
  adminStart(action: string, params: object): Promise<MatchJob>;
  adminJob(id: string): Promise<MatchJob>;
  adminAction(action: string, params: Record<string, string | number>): Promise<Record<string, unknown>>;
  manualList(): Promise<ManualList>;
  manualRegister(record: Record<string, string>): Promise<{ ok: boolean; record_id: string }>;
  manualImport(rows: Array<Record<string, string>>, dryRun: boolean): Promise<Record<string, unknown>>;
  manualUndo(recordIds: string[]): Promise<{ ok: boolean; removed: number }>;
  entities(q: string, limit?: number, filters?: SearchFilters): Promise<SearchResult>;
  normalizePreview(text: string): Promise<Record<string, string>>;
  entity(id: string, offset?: number, limit?: number): Promise<EntityDetail>;
  health(): Promise<HealthInfo>;
  matchSample(): Promise<{ record: Record<string, string> }>;
  startRecommendation(record: Record<string, string>, thresholds?: { auto: number; review: number }): Promise<MatchJob>;
  recommendation(id: string): Promise<MatchJob>;
  review(minP: number, offset: number, limit: number, nayose?: string): Promise<ReviewPage>;
  metrics(): Promise<MetricsViewData>;
  metricsFor(run: string): Promise<MetricsViewData>;
  confusion(): Promise<ConfusionData>;
  compare(l: string, r: string): Promise<Record<string, unknown>[]>;
  recommend(record: Record<string, string>): Promise<RecommendResult>;
  loadDecisions(): Promise<Array<{ left: string; right: string }>>;
  saveDecisions(items: Array<{ left: string; right: string; ts: string; reviewer?: string }>): Promise<void>;
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