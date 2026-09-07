// Infra: ApiAdapter (QueryPort over typed FastAPI endpoints) is a PURE
// TRANSPORT — every request goes out, nothing is cached. Caching is a
// separate concern owned by MemoProxy (Decorator/Proxy pattern): it wraps any
// QueryPort and memoizes read ops, cleared on run switch. createPort is the
// Factory: it composes the default transport with the memo decorator.
// SQL lives SERVER-SIDE in typed endpoints; a raw-SQL door exists only
// behind NAYOSE_DEBUG_SQL.
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
  EntityHit,
  HealthInfo,
  ManualList,
  MetricsViewData,
  MatchJob,
  QueryPort,
  RecommendResult,
  ReviewPage,
  RunInfo,
  SearchFilters,
  SearchResult,
} from "./port.js";

export class ApiError extends Error {}

function adaptSearchResult(data: unknown): SearchResult {
  if (!data || typeof data !== "object" || !Array.isArray((data as { rows?: unknown }).rows)) throw new ApiError("search_schema_mismatch");
  return data as SearchResult;
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
  const timeout = setTimeout(() => controller.abort(), 20000);
  const headers = new Headers(init?.headers ?? {});
  // Bearer adapter: the transport attaches a deployment token when present, so
  // the GUI works behind NAYOSE_API_TOKEN too. The token is stored under
  // "nayose.token" (e.g. set by an authed wrapper page) — views never think
  // about auth; the Facade owns that sharp edge.
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
    if (controller.signal.aborted) throw new ApiError("request_timeout");
    throw err;
  } finally {
    clearTimeout(timeout);
  }
}

export class ApiAdapter implements QueryPort {
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
  adminDatasetRows(generationId: string, table: string, query: string, offset: number, limit: number): Promise<DatasetRows> {
    const params = new URLSearchParams({ table, q: query, offset: String(offset), limit: String(limit) });
    return fetchJson(`/api/admin/generations/${encodeURIComponent(generationId)}/records?${params}`).then(d => d as DatasetRows);
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

// Decorator/Proxy: memoizes read ops of any QueryPort, keyed by (run, op,
// args); run() clears the cache. Mutations and command-adjacent reads
// (recommend/loadDecisions/saveDecisions) pass through uncached.
export class MemoProxy implements QueryPort {
  private runId = "";
  private cache = new Map<string, Promise<unknown>>();

  constructor(private readonly inner: QueryPort) {
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
  adminDatasetRows(generationId: string, table: string, query: string, offset: number, limit: number): Promise<DatasetRows> {
    return this.inner.adminDatasetRows(generationId, table, query, offset, limit);
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
export function createPort(transport?: QueryPort): QueryPort {
  return new MemoProxy(transport ?? new ApiAdapter());
}