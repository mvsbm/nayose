// ApiAdapter is the transport for typed FastAPI endpoints. It does not cache;
// MemoProxy wraps read operations and clears them when the run changes. SQL
// lives server-side in typed endpoints; a raw-SQL door exists only behind
// NAYOSE_DEBUG_SQL.
import {
  AdminCapability,
  AdminConfig,
  AdminState,
  ConfusionData,
  DecisionSnapshot,
  DatasetGeneration,
  DatasetRows,
  DatasetSummary,
  GenerationCatalog,
  GenerationControls,
  GenerationPreview,
  EntityDetail,
  EntityExplorerPage,
  EntityGraph,
  EntityHit,
  EntitySort,
  HealthInfo,
  ManualList,
  MetricsViewData,
  MatchJob,
  WorkbenchPort,
  RecommendResult,
  ReviewPage,
  RunInfo,
  SearchFilters,
  SearchResult,
} from "./port.js";
import { adaptDatasetRows } from "./dataset-adapter.js";
import { ApiError } from "./api-error.js";
import { fetchJson } from "./http.js";

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function invalid(code: string): never {
  throw new ApiError(code);
}

function object(data: unknown, code: string): Record<string, unknown> {
  return isObject(data) ? data : invalid(code);
}

function array(data: unknown, code: string): unknown[] {
  return Array.isArray(data) ? data : invalid(code);
}

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function records(data: unknown[], code: string): Record<string, unknown>[] {
  return data.map(item => isObject(item) ? item : invalid(code));
}

function adaptRunList(data: unknown): RunInfo[] {
  return records(array(data, "runs_schema_mismatch"), "runs_schema_mismatch").map(run => {
    if (typeof run.id !== "string" || (run.path !== undefined && typeof run.path !== "string")) invalid("runs_schema_mismatch");
    return { ...run, path: typeof run.path === "string" ? run.path : "" } as unknown as RunInfo;
  });
}

function adaptAdminCapability(data: unknown): AdminCapability {
  const result = object(data, "admin_capability_schema_mismatch");
  if (typeof result.capable !== "boolean") invalid("admin_capability_schema_mismatch");
  return result as unknown as AdminCapability;
}

function adaptSearchResult(data: unknown): SearchResult {
  const result = object(data, "search_schema_mismatch");
  const rows = records(array(result.rows, "search_schema_mismatch"), "search_schema_mismatch");
  if (rows.some(row => typeof row.nayose_id !== "string" || typeof row.name_kanji !== "string") || typeof result.semantic !== "boolean") invalid("search_schema_mismatch");
  for (const key of ["exact", "semantic_results", "nearest"]) {
    if (result[key] !== undefined) {
      const candidates = records(array(result[key], "search_schema_mismatch"), "search_schema_mismatch");
      if (candidates.some(row => typeof row.nayose_id !== "string" || typeof row.name_kanji !== "string")) invalid("search_schema_mismatch");
    }
  }
  return result as unknown as SearchResult;
}

function adaptEntityDetail(data: unknown): EntityDetail {
  const result = object(data, "entity_schema_mismatch");
  if (!isObject(result.entity) || !Array.isArray(result.members) || records(result.members, "entity_schema_mismatch").some(member => typeof member.record_id !== "string") || !finite(result.total) || !finite(result.offset) || !finite(result.limit) || !isObject(result.comparisons) || typeof result.merge_evidence_available !== "boolean") invalid("entity_schema_mismatch");
  return result as unknown as EntityDetail;
}

function adaptHealth(data: unknown): HealthInfo {
  const result = object(data, "health_schema_mismatch");
  if (typeof result.status !== "string" || (result.run !== undefined && result.run !== null && typeof result.run !== "string") || (result.engine_warm !== undefined && typeof result.engine_warm !== "boolean") || (result.stats !== undefined && records(array(result.stats, "health_schema_mismatch"), "health_schema_mismatch").some(stat => typeof stat.endpoint !== "string" || !finite(stat.hits) || !finite(stat.total_ms)))) invalid("health_schema_mismatch");
  return result as unknown as HealthInfo;
}

function adaptMatchJob(data: unknown): MatchJob {
  const result = object(data, "job_schema_mismatch");
  const stages = new Set(["queued", "validating", "loading", "retrieving", "scoring", "aggregating", "complete", "failed", "cancelled"]);
  const progress = result.progress;
  const validProgress = progress === undefined || progress === null || (isObject(progress) && (progress.phase === undefined || typeof progress.phase === "string") && (progress.pct === undefined || progress.pct === null || finite(progress.pct)) && (progress.eta_seconds === undefined || progress.eta_seconds === null || finite(progress.eta_seconds)) && (progress.done === undefined || finite(progress.done)) && (progress.total === undefined || finite(progress.total)));
  if (typeof result.id !== "string" || typeof result.run_id !== "string" || typeof result.stage !== "string" || !stages.has(result.stage) || !finite(result.elapsed_ms) || !Array.isArray(result.timeline) || result.timeline.some(item => !isObject(item) || typeof item.stage !== "string" || !finite(item.elapsed_ms)) || !validProgress || (result.error !== null && result.error !== undefined && typeof result.error !== "string") || (result.result !== null && result.result !== undefined && !isObject(result.result))) invalid("job_schema_mismatch");
  return result as unknown as MatchJob;
}

function adaptMetrics(data: unknown): MetricsViewData {
  const result = object(data, "metrics_schema_mismatch");
  if (!Array.isArray(result.kpis) || result.kpis.some(item => !Array.isArray(item) || item.length !== 2 || typeof item[0] !== "string" || !finite(item[1])) || !isObject(result.extras) || Object.values(result.extras).some(value => !finite(value))) invalid("metrics_schema_mismatch");
  return result as unknown as MetricsViewData;
}

function adaptReview(data: unknown): ReviewPage {
  const result = object(data, "review_schema_mismatch");
  if (!finite(result.total) || !Array.isArray(result.rows) || records(result.rows, "review_schema_mismatch").some(row => typeof row.left_record_id !== "string" || typeof row.right_record_id !== "string" || !finite(row.pair_probability)) || !Array.isArray(result.histogram) || result.histogram.some(item => !Array.isArray(item) || item.length !== 2 || !finite(item[0]) || !finite(item[1]))) invalid("review_schema_mismatch");
  return result as unknown as ReviewPage;
}

function adaptDecisionSnapshot(data: unknown): DecisionSnapshot {
  const result = object(data, "decisions_schema_mismatch");
  if (!Array.isArray(result.items) || records(result.items, "decisions_schema_mismatch").some(item => typeof item.left !== "string" || typeof item.right !== "string") || !finite(result.revision)) invalid("decisions_schema_mismatch");
  return result as unknown as DecisionSnapshot;
}

function adaptRecordObject(data: unknown, code: string): Record<string, unknown> {
  return object(data, code);
}

function adaptEntityExplorerPage(data: unknown): EntityExplorerPage {
  if (!isObject(data)) throw new ApiError("entity_explorer_schema_mismatch");
  const page = data as Partial<EntityExplorerPage>;
  const validSorts = new Set(["relevance", "records", "name", "last_seen"]);
  const validModes = new Set(["exact", "hybrid_fallback"]);
  if (!Array.isArray(page.rows) || page.rows.some(row => !isObject(row) || typeof row.nayose_id !== "string" || typeof row.name_kanji !== "string") || typeof page.total !== "number" || typeof page.offset !== "number" || typeof page.limit !== "number" || typeof page.query !== "string" || !validSorts.has(page.sort ?? "") || !validModes.has(page.search_mode ?? "") || typeof page.total_is_exact !== "boolean" || (page.cursor !== undefined && page.cursor !== null && typeof page.cursor !== "string") || (page.next_cursor !== undefined && page.next_cursor !== null && typeof page.next_cursor !== "string") || (page.has_more !== undefined && typeof page.has_more !== "boolean") || !isObject(page.filters) || !isObject(page.query_plan) || !isObject(page.search_trace)) {
    throw new ApiError("entity_explorer_schema_mismatch");
  }
  return page as EntityExplorerPage;
}

function adaptEntityGraph(data: unknown): EntityGraph {
  if (!isObject(data)) throw new ApiError("entity_graph_schema_mismatch");
  const graph = data as Partial<EntityGraph>;
  const validKinds = new Set(["entity", "record", "policy", "agency", "channel"]);
  const validEdgeKinds = new Set(["observed", "potential"]);
  const validNode = (node: unknown): boolean => isObject(node) && typeof node.id === "string" && typeof node.label === "string" && validKinds.has(String(node.kind)) && isObject(node.metadata);
  const validEdge = (edge: unknown): boolean => isObject(edge) && typeof edge.source === "string" && typeof edge.target === "string" && typeof edge.relation === "string" && validEdgeKinds.has(String(edge.kind)) && isObject(edge.evidence);
  if (typeof graph.entity_id !== "string" || !Array.isArray(graph.nodes) || !Array.isArray(graph.edges) || graph.nodes.some(node => !validNode(node)) || graph.edges.some(edge => !validEdge(edge)) || typeof graph.truncated !== "boolean" || typeof graph.potential_matches_available !== "boolean") {
    throw new ApiError("entity_graph_schema_mismatch");
  }
  return graph as EntityGraph;
}

function adaptAdminConfig(data: unknown): AdminConfig {
  if (!isObject(data) || typeof data.schema_version !== "number" || typeof data.config_hash !== "string" || typeof data.source !== "string" || data.editable !== false || !isObject(data.sections)) {
    throw new ApiError("admin_config_schema_mismatch");
  }
  const sections = data.sections as Record<string, unknown>;
  if (Object.values(sections).some(section => !isObject(section))) throw new ApiError("admin_config_schema_mismatch");
  return data as unknown as AdminConfig;
}

function adaptGenerationPreview(data: unknown): GenerationPreview {
  if (!data || typeof data !== "object") throw new ApiError("preview_schema_mismatch");
  const preview = data as Partial<GenerationPreview>;
  if (!isObject(preview.samples) || !isObject(preview.samples.average) || !isObject(preview.time_window) || !isObject(preview.dataset_impact) || !isObject(preview.counts) || !Array.isArray(preview.examples) || preview.examples.some(example => !isObject(example)) || !Array.isArray(preview.events) || !Array.isArray(preview.query_mix) || !isObject(preview.missingness) || !Array.isArray(preview.warnings) || !isObject(preview.estimate_note) || !finite(preview.weighted_band_scale)) {
    throw new ApiError("preview_schema_mismatch");
  }
  return preview as GenerationPreview;
}

export class ApiAdapter implements WorkbenchPort {
  private runId = "";

  run(id: string): void {
    this.runId = id;
  }

  runs(): Promise<RunInfo[]> {
    return fetchJson("/api/runs").then(adaptRunList);
  }
  adminCapability(): Promise<AdminCapability> {
    return fetchJson("/api/admin/capable").then(adaptAdminCapability);
  }
  adminConfig(signal?: AbortSignal): Promise<AdminConfig> {
    return fetchJson("/api/admin/config", { signal }).then(adaptAdminConfig);
  }
  adminState(signal?: AbortSignal): Promise<AdminState> {
    return fetchJson("/api/admin/state", { signal }).then(d => {
      const result = object(d, "admin_state_schema_mismatch");
      if (!isObject(result.profile) || !isObject(result.settings) || !Array.isArray(result.runs) || !finite(result.manual_count) || !Array.isArray(result.audit)) invalid("admin_state_schema_mismatch");
      return result as unknown as AdminState;
    });
  }
  adminGenerationConfig(signal?: AbortSignal): Promise<GenerationCatalog> {
    return fetchJson("/api/admin/generation-config", { signal }).then(d => {
      const result = object(d, "generation_config_schema_mismatch");
      if (!Array.isArray(result.profiles) || !Array.isArray(result.difficulties) || !Array.isArray(result.events) || !Array.isArray(result.channels) || !Array.isArray(result.query_scenarios) || !isObject(result.defaults) || !isObject(result.bounds)) invalid("generation_config_schema_mismatch");
      return result as unknown as GenerationCatalog;
    });
  }
  adminGenerationPreview(controls: GenerationControls, signal?: AbortSignal): Promise<GenerationPreview> {
    return fetchJson("/api/admin/generation-preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ controls }),
      signal,
    }).then(adaptGenerationPreview);
  }
  adminGenerations(signal?: AbortSignal): Promise<DatasetGeneration[]> {
    return fetchJson("/api/admin/generations", { signal }).then(d => records(array(d, "generations_schema_mismatch"), "generations_schema_mismatch").map(item => {
      if (typeof item.id !== "string" || typeof item.created_at !== "string" || typeof item.fingerprint !== "string" || typeof item.current !== "boolean") invalid("generations_schema_mismatch");
      return item as unknown as DatasetGeneration;
    }));
  }
  adminDatasetSummary(generationId: string, signal?: AbortSignal): Promise<DatasetSummary> {
    return fetchJson(`/api/admin/generations/${encodeURIComponent(generationId)}/summary`, { signal }).then(d => {
      const result = object(d, "dataset_summary_schema_mismatch");
      if (typeof result.id !== "string" || typeof result.created_at !== "string" || typeof result.fingerprint !== "string" || !isObject(result.config) || !isObject(result.counts) || !Array.isArray(result.tables)) invalid("dataset_summary_schema_mismatch");
      return result as unknown as DatasetSummary;
    });
  }
  adminDatasetRows(
    generationId: string,
    table: string,
    query: string,
    offset: number,
    limit: number,
    filters: Record<string, string> = {},
    includeTotal = false,
    after = "",
    signal?: AbortSignal,
  ): Promise<DatasetRows> {
    const params = new URLSearchParams({
      table,
      q: query,
      offset: String(offset),
      limit: String(limit),
      count: String(includeTotal),
    });
    if (after) params.set("after", after);
    if (Object.keys(filters).length) params.set("filters", JSON.stringify(filters));
    return fetchJson(`/api/admin/generations/${encodeURIComponent(generationId)}/records?${params}`, { signal }).then(adaptDatasetRows);
  }
  adminStart(action: string, params: object, signal?: AbortSignal): Promise<MatchJob> {
    return fetchJson("/api/admin/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, params }),
      signal,
    }).then(adaptMatchJob);
  }
  adminJob(id: string, signal?: AbortSignal): Promise<MatchJob> {
    return fetchJson(`/api/admin/jobs/${encodeURIComponent(id)}`, { signal }).then(adaptMatchJob);
  }
  adminAction(action: string, params: Record<string, string | number>, signal?: AbortSignal): Promise<Record<string, unknown>> {
    return fetchJson("/api/admin/actions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, params }),
      signal,
    }).then(d => adaptRecordObject(d, "admin_action_schema_mismatch"));
  }
  manualList(signal?: AbortSignal): Promise<ManualList> {
    return fetchJson("/api/data-entry", { signal }).then(d => {
      const result = object(d, "manual_list_schema_mismatch");
      if (!finite(result.count) || !Array.isArray(result.rows) || records(result.rows, "manual_list_schema_mismatch").some(row => typeof row.record_id !== "string")) invalid("manual_list_schema_mismatch");
      return result as unknown as ManualList;
    });
  }
  manualRegister(record: Record<string, string>, signal?: AbortSignal): Promise<{ ok: boolean; record_id: string }> {
    return fetchJson("/api/data-entry/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ record }),
      signal,
    }).then(d => {
      const result = object(d, "manual_register_schema_mismatch");
      if (result.ok !== true || typeof result.record_id !== "string") invalid("manual_register_schema_mismatch");
      return result as { ok: boolean; record_id: string };
    });
  }
  manualImport(rows: Array<Record<string, string>>, dryRun: boolean, signal?: AbortSignal): Promise<Record<string, unknown>> {
    return fetchJson("/api/data-entry/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rows, dry_run: dryRun }),
      signal,
    }).then(d => adaptRecordObject(d, "manual_import_schema_mismatch"));
  }
  manualUndo(recordIds: string[], signal?: AbortSignal): Promise<{ ok: boolean; removed: number }> {
    return fetchJson("/api/data-entry/undo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ record_ids: recordIds }),
      signal,
    }).then(d => {
      const result = object(d, "manual_undo_schema_mismatch");
      if (result.ok !== true || !finite(result.removed)) invalid("manual_undo_schema_mismatch");
      return result as { ok: boolean; removed: number };
    });
  }

  entities(q: string, limit?: number, filters?: SearchFilters, signal?: AbortSignal): Promise<SearchResult> {
    const params = new URLSearchParams({ q, run_id: this.runId });
    if (limit) params.set("limit", String(limit));
    if (filters?.party_type) params.set("party_type", filters.party_type);
    if (filters?.min_record_count) params.set("min_record_count", String(filters.min_record_count));
    return fetchJson(`/api/entities?${params.toString()}`, { signal }).then(adaptSearchResult);
  }
  entityExplorer(q: string, offset: number, limit: number, sort: EntitySort, filters?: SearchFilters, cursor?: string, signal?: AbortSignal): Promise<EntityExplorerPage> {
    const params = new URLSearchParams({ q, run_id: this.runId, offset: String(offset), limit: String(limit), sort });
    if (cursor) params.set("cursor", cursor);
    if (filters?.party_type) params.set("party_type", filters.party_type);
    if (filters?.min_record_count) params.set("min_record_count", String(filters.min_record_count));
    return fetchJson(`/api/entities/explorer?${params.toString()}`, { signal }).then(adaptEntityExplorerPage);
  }
  entityGraph(id: string, includePotential = false, signal?: AbortSignal): Promise<EntityGraph> {
    return fetchJson(`/api/entity/${encodeURIComponent(id)}/graph?run_id=${encodeURIComponent(this.runId)}&include_potential=${includePotential ? "true" : "false"}`, { signal }).then(adaptEntityGraph);
  }
  normalizePreview(text: string, signal?: AbortSignal): Promise<Record<string, string>> {
    return fetchJson(`/api/normalize?text=${encodeURIComponent(text)}`, { signal }).then(d => {
      const result = object(d, "normalize_schema_mismatch");
      if (Object.values(result).some(value => typeof value !== "string")) invalid("normalize_schema_mismatch");
      return result as Record<string, string>;
    });
  }
  entity(id: string, offset = 0, limit = 25, signal?: AbortSignal): Promise<EntityDetail> {
    return fetchJson(`/api/entity/${encodeURIComponent(id)}?run_id=${encodeURIComponent(this.runId)}&offset=${offset}&limit=${limit}`, { signal }).then(adaptEntityDetail);
  }
  health(signal?: AbortSignal): Promise<HealthInfo> {
    return fetchJson("/api/health/details", { signal }).then(adaptHealth);
  }
  matchSample(signal?: AbortSignal): Promise<{ record: Record<string, string> }> {
    return fetchJson(`/api/match-sample?run_id=${encodeURIComponent(this.runId)}`, { signal }).then(d => {
      const result = object(d, "match_sample_schema_mismatch");
      if (!isObject(result.record) || Object.values(result.record).some(value => typeof value !== "string")) invalid("match_sample_schema_mismatch");
      return result as { record: Record<string, string> };
    });
  }
  startRecommendation(
    record: Record<string, string>,
    thresholds?: { auto: number; review: number },
    signal?: AbortSignal,
  ): Promise<MatchJob> {
    return fetchJson("/api/recommendations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: this.runId, record, ...(thresholds ? { thresholds } : {}) }),
      signal,
    }).then(adaptMatchJob);
  }
  recommendation(id: string, signal?: AbortSignal): Promise<MatchJob> {
    return fetchJson(`/api/recommendations/${encodeURIComponent(id)}`, { signal }).then(adaptMatchJob);
  }
  review(minP: number, offset: number, limit: number, nayose?: string, signal?: AbortSignal): Promise<ReviewPage> {
    const n = nayose ? `&nayose=${encodeURIComponent(nayose)}` : "";
    return fetchJson(
      `/api/review?run_id=${encodeURIComponent(this.runId)}&min_p=${minP}&offset=${offset}&limit=${limit}${n}`,
      { signal },
    ).then(adaptReview);
  }
  metrics(signal?: AbortSignal): Promise<MetricsViewData> {
    return this.metricsFor(this.runId, signal);
  }
  metricsFor(run: string, signal?: AbortSignal): Promise<MetricsViewData> {
    return fetchJson(`/api/metrics?run_id=${encodeURIComponent(run)}`, { signal }).then(adaptMetrics);
  }
  confusion(signal?: AbortSignal): Promise<ConfusionData> {
    return fetchJson(`/api/confusion?run_id=${encodeURIComponent(this.runId)}`, { signal }).then(d => {
      const result = object(d, "confusion_schema_mismatch");
      if (!isObject(result.pairs) || Object.values(result.pairs).some(value => !finite(value)) || !Array.isArray(result.scenarios) || result.scenarios.some(item => !isObject(item) || typeof item.scenario !== "string" || !isObject(item.actions) || Object.values(item.actions).some(value => !finite(value))) || !isObject(result.decisions)) invalid("confusion_schema_mismatch");
      return result as unknown as ConfusionData;
    });
  }
  compare(l: string, r: string, signal?: AbortSignal): Promise<Record<string, unknown>[]> {
    return fetchJson(
      `/api/compare?run_id=${encodeURIComponent(this.runId)}&left=${encodeURIComponent(l)}&right=${encodeURIComponent(r)}`,
      { signal },
    ).then(d => {
      const result = object(d, "compare_schema_mismatch");
      return records(array(result.rows, "compare_schema_mismatch"), "compare_schema_mismatch");
    });
  }
  recommend(record: Record<string, string>): Promise<RecommendResult> {
    return fetchJson("/api/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: this.runId, record }),
    }).then(d => {
      const result = object(d, "recommend_schema_mismatch");
      if (typeof result.action !== "string" || !finite(result.top_probability) || !finite(result.margin) || !Array.isArray(result.candidates) || typeof result.reason !== "string" || !isObject(result.diagnostics) || typeof result.scoring_mode !== "string") invalid("recommend_schema_mismatch");
      return result as unknown as RecommendResult;
    });
  }
  loadDecisions(runId = this.runId, signal?: AbortSignal): Promise<DecisionSnapshot> {
    return fetchJson(`/api/decisions?run_id=${encodeURIComponent(runId)}`, { signal }).then(adaptDecisionSnapshot);
  }
  saveDecisions(
    items: Array<{ left: string; right: string; ts: string; reviewer?: string }>,
    revision: number,
    runId = this.runId,
    signal?: AbortSignal,
  ): Promise<number> {
    const reviewer = items.find((i) => i.reviewer)?.reviewer ?? "";
    return fetchJson("/api/decisions", {
      method: "PUT",
      headers: { "Content-Type": "application/json", ...(reviewer ? { "X-Reviewer": reviewer } : {}) },
      body: JSON.stringify({ run_id: runId, items, revision }),
      signal,
    }).then((data) => {
      const detail = data as { revision?: number };
      if (typeof detail.revision !== "number") throw new ApiError("invalid_decision_revision");
      return detail.revision;
    });
  }
}

export { MemoProxy } from "./memo-proxy.js";
import { MemoProxy } from "./memo-proxy.js";
// Factory: composes the production port. A WASM data adapter can be swapped
// in later by passing it as the transport (OCP: no composition changes needed).
export function createPort(transport?: WorkbenchPort): WorkbenchPort {
  return new MemoProxy(transport ?? new ApiAdapter());
}