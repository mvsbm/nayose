// ApiAdapter is the transport for typed FastAPI endpoints. It does not cache;
// MemoProxy wraps read operations and clears them when the run changes. SQL
// lives server-side in typed endpoints; a raw-SQL door exists only behind
// NAYOSE_DEBUG_SQL.
import {
  AdminCapability,
  AdminState,
  ConfusionData,
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

function adaptSearchResult(data: unknown): SearchResult {
  if (!data || typeof data !== "object" || !Array.isArray((data as { rows?: unknown }).rows)) throw new ApiError("search_schema_mismatch");
  return data as SearchResult;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function adaptEntityExplorerPage(data: unknown): EntityExplorerPage {
  if (!isObject(data)) throw new ApiError("entity_explorer_schema_mismatch");
  const page = data as Partial<EntityExplorerPage>;
  const validSorts = new Set(["relevance", "records", "name", "last_seen"]);
  const validModes = new Set(["exact", "hybrid_fallback"]);
  if (!Array.isArray(page.rows) || typeof page.total !== "number" || typeof page.offset !== "number" || typeof page.limit !== "number" || typeof page.query !== "string" || !validSorts.has(page.sort ?? "") || !validModes.has(page.search_mode ?? "") || typeof page.total_is_exact !== "boolean" || !isObject(page.filters) || !isObject(page.query_plan) || !isObject(page.search_trace)) {
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

function adaptGenerationPreview(data: unknown): GenerationPreview {
  if (!data || typeof data !== "object") throw new ApiError("preview_schema_mismatch");
  const preview = data as Partial<GenerationPreview>;
  if (!preview.samples || typeof preview.samples !== "object" || !preview.samples.average || !preview.time_window || !preview.dataset_impact || !Array.isArray(preview.examples)) {
    throw new ApiError("preview_schema_mismatch");
  }
  return preview as GenerationPreview;
}

async function fetchJson(path: string, init?: RequestInit): Promise<unknown> {
  const controller = new AbortController();
  const externalSignal = init?.signal;
  const abortFromCaller = (): void => controller.abort();
  if (externalSignal?.aborted) controller.abort();
  else externalSignal?.addEventListener("abort", abortFromCaller, { once: true });
  const timeout = setTimeout(() => controller.abort(), 20000);
  const headers = new Headers(init?.headers ?? {});
  // Attach the deployment token when present. Views do not handle auth.
  try {
    const token = localStorage.getItem("nayose.token");
    if (token) headers.set("Authorization", `Bearer ${token}`);
  } catch { /* storage unavailable: request stays unauthenticated */ }
  try {
    const res = await fetch(path, { ...init, headers, signal: controller.signal });
    if (!res.ok) {
      const detail = await res.json().catch(() => null);
      throw new ApiError(typeof detail?.detail === "string" ? detail.detail : `HTTP ${res.status}`);
    }
    return await res.json();
  } catch (err) {
    if (externalSignal?.aborted) throw new ApiError("request_cancelled");
    if (controller.signal.aborted) throw new ApiError("request_timeout");
    throw err;
  } finally {
    clearTimeout(timeout);
    externalSignal?.removeEventListener("abort", abortFromCaller);
  }
}

export class ApiAdapter implements WorkbenchPort {
  private runId = "";

  run(id: string): void {
    this.runId = id;
  }

  runs(): Promise<RunInfo[]> {
    return fetchJson("/api/runs").then(d => d as RunInfo[]);
  }
  adminCapability(): Promise<AdminCapability> {
    return fetchJson("/api/admin/capable").then(d => d as AdminCapability);
  }
  adminState(): Promise<AdminState> {
    return fetchJson("/api/admin/state").then(d => d as AdminState);
  }
  adminGenerationConfig(): Promise<GenerationCatalog> {
    return fetchJson("/api/admin/generation-config").then(d => d as GenerationCatalog);
  }
  adminGenerationPreview(controls: GenerationControls): Promise<GenerationPreview> {
    return fetchJson("/api/admin/generation-preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ controls }),
    }).then(adaptGenerationPreview);
  }
  adminGenerations(): Promise<DatasetGeneration[]> {
    return fetchJson("/api/admin/generations").then(d => d as DatasetGeneration[]);
  }
  adminDatasetSummary(generationId: string): Promise<DatasetSummary> {
    return fetchJson(`/api/admin/generations/${encodeURIComponent(generationId)}/summary`).then(d => d as DatasetSummary);
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
  adminStart(action: string, params: object): Promise<MatchJob> {
    return fetchJson("/api/admin/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, params }),
    }).then(d => d as MatchJob);
  }
  adminJob(id: string): Promise<MatchJob> {
    return fetchJson(`/api/admin/jobs/${encodeURIComponent(id)}`).then(d => d as MatchJob);
  }
  adminAction(action: string, params: Record<string, string | number>): Promise<Record<string, unknown>> {
    return fetchJson("/api/admin/actions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, params }),
    }).then(d => d as Record<string, unknown>);
  }
  manualList(): Promise<ManualList> {
    return fetchJson("/api/data-entry").then(d => d as ManualList);
  }
  manualRegister(record: Record<string, string>): Promise<{ ok: boolean; record_id: string }> {
    return fetchJson("/api/data-entry/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ record }),
    }).then(d => d as { ok: boolean; record_id: string });
  }
  manualImport(rows: Array<Record<string, string>>, dryRun: boolean): Promise<Record<string, unknown>> {
    return fetchJson("/api/data-entry/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rows, dry_run: dryRun }),
    }).then(d => d as Record<string, unknown>);
  }
  manualUndo(recordIds: string[]): Promise<{ ok: boolean; removed: number }> {
    return fetchJson("/api/data-entry/undo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ record_ids: recordIds }),
    }).then(d => d as { ok: boolean; removed: number });
  }

  entities(q: string, limit?: number, filters?: SearchFilters): Promise<SearchResult> {
    const params = new URLSearchParams({ q, run_id: this.runId });
    if (limit) params.set("limit", String(limit));
    if (filters?.party_type) params.set("party_type", filters.party_type);
    if (filters?.min_record_count) params.set("min_record_count", String(filters.min_record_count));
    return fetchJson(`/api/entities?${params.toString()}`).then(adaptSearchResult);
  }
  entityExplorer(q: string, offset: number, limit: number, sort: EntitySort, filters?: SearchFilters): Promise<EntityExplorerPage> {
    const params = new URLSearchParams({ q, run_id: this.runId, offset: String(offset), limit: String(limit), sort });
    if (filters?.party_type) params.set("party_type", filters.party_type);
    if (filters?.min_record_count) params.set("min_record_count", String(filters.min_record_count));
    return fetchJson(`/api/entities/explorer?${params.toString()}`).then(adaptEntityExplorerPage);
  }
  entityGraph(id: string, includePotential = false): Promise<EntityGraph> {
    return fetchJson(`/api/entity/${encodeURIComponent(id)}/graph?run_id=${encodeURIComponent(this.runId)}&include_potential=${includePotential ? "true" : "false"}`).then(adaptEntityGraph);
  }
  normalizePreview(text: string): Promise<Record<string, string>> {
    return fetchJson(`/api/normalize?text=${encodeURIComponent(text)}`).then(d => d as Record<string, string>);
  }
  entity(id: string, offset = 0, limit = 25): Promise<EntityDetail> {
    return fetchJson(`/api/entity/${encodeURIComponent(id)}?run_id=${encodeURIComponent(this.runId)}&offset=${offset}&limit=${limit}`).then(
      (d) => d as EntityDetail,
    );
  }
  health(): Promise<HealthInfo> {
    return fetchJson("/api/health").then(d => d as HealthInfo);
  }
  matchSample(): Promise<{ record: Record<string, string> }> {
    return fetchJson(`/api/match-sample?run_id=${encodeURIComponent(this.runId)}`).then(d => d as { record: Record<string, string> });
  }
  startRecommendation(
    record: Record<string, string>,
    thresholds?: { auto: number; review: number },
  ): Promise<MatchJob> {
    return fetchJson("/api/recommendations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: this.runId, record, ...(thresholds ? { thresholds } : {}) }),
    }).then(d => d as MatchJob);
  }
  recommendation(id: string): Promise<MatchJob> {
    return fetchJson(`/api/recommendations/${encodeURIComponent(id)}`).then(d => d as MatchJob);
  }
  review(minP: number, offset: number, limit: number, nayose?: string): Promise<ReviewPage> {
    const n = nayose ? `&nayose=${encodeURIComponent(nayose)}` : "";
    return fetchJson(
      `/api/review?run_id=${encodeURIComponent(this.runId)}&min_p=${minP}&offset=${offset}&limit=${limit}${n}`,
    ).then((d) => d as ReviewPage);
  }
  metrics(): Promise<MetricsViewData> {
    return this.metricsFor(this.runId);
  }
  metricsFor(run: string): Promise<MetricsViewData> {
    return fetchJson(`/api/metrics?run_id=${encodeURIComponent(run)}`).then((d) => d as MetricsViewData);
  }
  confusion(): Promise<ConfusionData> {
    return fetchJson(`/api/confusion?run_id=${encodeURIComponent(this.runId)}`).then((d) => d as ConfusionData);
  }
  compare(l: string, r: string): Promise<Record<string, unknown>[]> {
    return fetchJson(
      `/api/compare?run_id=${encodeURIComponent(this.runId)}&left=${encodeURIComponent(l)}&right=${encodeURIComponent(r)}`,
    ).then((d) => (d as { rows: Record<string, unknown>[] }).rows);
  }
  recommend(record: Record<string, string>): Promise<RecommendResult> {
    return fetchJson("/api/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: this.runId, record }),
    }).then((d) => d as RecommendResult);
  }
  loadDecisions(): Promise<Array<{ left: string; right: string }>> {
    return fetchJson(`/api/decisions?run_id=${encodeURIComponent(this.runId)}`).then(
      (d) => (d as { items: Array<{ left: string; right: string }> }).items,
    );
  }
  async saveDecisions(items: Array<{ left: string; right: string; ts: string; reviewer?: string }>): Promise<void> {
    const reviewer = items.find((i) => i.reviewer)?.reviewer ?? "";
    const res = await fetch("/api/decisions", {
      method: "PUT",
      headers: { "Content-Type": "application/json", ...(reviewer ? { "X-Reviewer": reviewer } : {}) },
      body: JSON.stringify({ run_id: this.runId, items }),
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => null);
      throw new ApiError((detail && detail.detail) || res.statusText);
    }
  }
}

// Memoizes read operations by run, operation, and arguments. run() clears the
// cache; mutations and command-adjacent reads pass through uncached.
export class MemoProxy implements WorkbenchPort {
  private runId = "";
  private cache = new Map<string, Promise<unknown>>();

  constructor(private readonly inner: WorkbenchPort) {
    this.inner = inner;
  }

  run(id: string): void {
    this.runId = id;
    this.cache.clear();
    this.inner.run(id);
  }
  runs(): Promise<RunInfo[]> {
    return this.memo("runs", "", () => this.inner.runs());
  }
  adminCapability(): Promise<AdminCapability> {
    return this.memo("adminCapability", "", () => this.inner.adminCapability());
  }
  adminState(): Promise<AdminState> {
    // mutation-adjacent read: actions mutate server state, so always refetch
    return this.inner.adminState();
  }
  adminGenerationConfig(): Promise<GenerationCatalog> {
    return this.memo("adminGenerationConfig", "", () => this.inner.adminGenerationConfig());
  }
  adminGenerationPreview(controls: GenerationControls): Promise<GenerationPreview> {
    return this.inner.adminGenerationPreview(controls);
  }
  adminGenerations(): Promise<DatasetGeneration[]> {
    return this.inner.adminGenerations();
  }
  adminDatasetSummary(generationId: string): Promise<DatasetSummary> {
    return this.inner.adminDatasetSummary(generationId);
  }
  adminDatasetRows(
    generationId: string,
    table: string,
    query: string,
    offset: number,
    limit: number,
    filters?: Record<string, string>,
    includeTotal?: boolean,
    after?: string,
    signal?: AbortSignal,
  ): Promise<DatasetRows> {
    return this.inner.adminDatasetRows(generationId, table, query, offset, limit, filters, includeTotal, after, signal);
  }
  adminStart(action: string, params: object): Promise<MatchJob> {
    return this.inner.adminStart(action, params);
  }
  adminJob(id: string): Promise<MatchJob> {
    return this.inner.adminJob(id);
  }
  adminAction(action: string, params: Record<string, string | number>): Promise<Record<string, unknown>> {
    return this.inner.adminAction(action, params);
  }
  manualList(): Promise<ManualList> {
    // mutation-adjacent read (register/import/undo), never cached
    return this.inner.manualList();
  }
  manualRegister(record: Record<string, string>): Promise<{ ok: boolean; record_id: string }> {
    return this.inner.manualRegister(record);
  }
  manualImport(rows: Array<Record<string, string>>, dryRun: boolean): Promise<Record<string, unknown>> {
    return this.inner.manualImport(rows, dryRun);
  }
  manualUndo(recordIds: string[]): Promise<{ ok: boolean; removed: number }> {
    return this.inner.manualUndo(recordIds);
  }
  private memo<T>(op: string, key: string, fetchFn: () => Promise<T>): Promise<T> {
    const k = `${this.runId}|${op}|${key}`;
    const hit = this.cache.get(k) as Promise<T> | undefined;
    if (hit) return hit;
    const p = fetchFn().catch((e) => {
      this.cache.delete(k);
      throw e;
    });
    this.cache.set(k, p);
    return p;
  }

  entities(q: string, limit?: number, filters?: SearchFilters): Promise<SearchResult> {
    return this.memo(
      "entities",
      `${q}:${limit ?? 50}:${JSON.stringify(filters ?? {})}`,
      () => this.inner.entities(q, limit, filters),
    );
  }
  entityExplorer(q: string, offset: number, limit: number, sort: EntitySort, filters?: SearchFilters): Promise<EntityExplorerPage> {
    return this.memo(
      "entityExplorer",
      `${q}:${offset}:${limit}:${sort}:${JSON.stringify(filters ?? {})}`,
      () => this.inner.entityExplorer(q, offset, limit, sort, filters),
    );
  }
  entityGraph(id: string, includePotential = false): Promise<EntityGraph> {
    return this.memo(
      "entityGraph",
      `${id}:${includePotential}`,
      () => this.inner.entityGraph(id, includePotential),
    );
  }
  normalizePreview(text: string): Promise<Record<string, string>> {
    return this.memo("normalize", text, () => this.inner.normalizePreview(text));
  }
  entity(id: string, offset = 0, limit = 25): Promise<EntityDetail> {
    return this.memo("entity", `${id}:${offset}:${limit}`, () => this.inner.entity(id, offset, limit));
  }
  health(): Promise<HealthInfo> {
    return this.memo("health", "", () => this.inner.health());
  }
  matchSample(): Promise<{ record: Record<string, string> }> {
    return this.memo("matchSample", "", () => this.inner.matchSample());
  }
  startRecommendation(
    record: Record<string, string>,
    thresholds?: { auto: number; review: number },
  ): Promise<MatchJob> {
    return this.inner.startRecommendation(record, thresholds);
  }
  recommendation(id: string): Promise<MatchJob> {
    return this.inner.recommendation(id);
  }
  review(minP: number, offset: number, limit: number, nayose?: string): Promise<ReviewPage> {
    return this.memo("review", `${minP}:${offset}:${limit}:${nayose ?? ""}`, () => this.inner.review(minP, offset, limit, nayose));
  }
  metrics(): Promise<MetricsViewData> {
    return this.memo("metrics", this.runId, () => this.inner.metrics());
  }
  metricsFor(run: string): Promise<MetricsViewData> {
    return this.memo("metrics", run, () => this.inner.metricsFor(run));
  }
  confusion(): Promise<ConfusionData> {
    return this.memo("confusion", "", () => this.inner.confusion());
  }
  compare(l: string, r: string): Promise<Record<string, unknown>[]> {
    return this.memo("compare", `${l}:${r}`, () => this.inner.compare(l, r));
  }
  recommend(record: Record<string, string>): Promise<RecommendResult> {
    return this.inner.recommend(record);
  }
  loadDecisions(): Promise<Array<{ left: string; right: string }>> {
    return this.inner.loadDecisions();
  }
  saveDecisions(items: Array<{ left: string; right: string; ts: string; reviewer?: string }>): Promise<void> {
    return this.inner.saveDecisions(items);
  }
}

// Factory: composes the production port. A WASM data adapter can be swapped
// in later by passing it as the transport (OCP: no composition changes needed).
export function createPort(transport?: WorkbenchPort): WorkbenchPort {
  return new MemoProxy(transport ?? new ApiAdapter());
}