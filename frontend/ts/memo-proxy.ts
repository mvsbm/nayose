import type {
  AdminCapability,
  AdminConfig,
  AdminState,
  ConfusionData,
  DecisionSnapshot,
  DatasetGeneration,
  DatasetRows,
  DatasetSummary,
  EntityDetail,
  EntityExplorerPage,
  EntityGraph,
  EntitySort,
  GenerationCatalog,
  GenerationControls,
  GenerationPreview,
  HealthInfo,
  ManualList,
  MetricsViewData,
  MatchJob,
  RecommendResult,
  ReviewPage,
  RunInfo,
  SearchFilters,
  SearchResult,
  WorkbenchPort,
} from "./port.js";

export class MemoProxy implements WorkbenchPort {
  private runId = "";
  private cache = new Map<string, Promise<unknown>>();

  constructor(private readonly inner: WorkbenchPort) {}

  run(id: string): void {
    this.runId = id;
    this.cache.clear();
    this.inner.run(id);
  }
  runs(): Promise<RunInfo[]> {
    return this.inner.runs();
  }
  adminCapability(): Promise<AdminCapability> {
    return this.memo("adminCapability", "", () => this.inner.adminCapability());
  }
  adminConfig(signal?: AbortSignal): Promise<AdminConfig> {
    if (signal) return this.inner.adminConfig(signal);
    return this.memo("adminConfig", "", () => this.inner.adminConfig());
  }
  adminState(signal?: AbortSignal): Promise<AdminState> {
    return this.inner.adminState(signal);
  }
  adminGenerationConfig(signal?: AbortSignal): Promise<GenerationCatalog> {
    if (signal) return this.inner.adminGenerationConfig(signal);
    return this.memo("adminGenerationConfig", "", () => this.inner.adminGenerationConfig());
  }
  adminGenerationPreview(controls: GenerationControls, signal?: AbortSignal): Promise<GenerationPreview> {
    return this.inner.adminGenerationPreview(controls, signal);
  }
  adminGenerations(signal?: AbortSignal): Promise<DatasetGeneration[]> {
    return this.inner.adminGenerations(signal);
  }
  adminDatasetSummary(generationId: string, signal?: AbortSignal): Promise<DatasetSummary> {
    return this.inner.adminDatasetSummary(generationId, signal);
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
  adminStart(action: string, params: object, signal?: AbortSignal): Promise<MatchJob> {
    return this.inner.adminStart(action, params, signal);
  }
  adminJob(id: string, signal?: AbortSignal): Promise<MatchJob> {
    return this.inner.adminJob(id, signal);
  }
  adminAction(action: string, params: Record<string, string | number>, signal?: AbortSignal): Promise<Record<string, unknown>> {
    return this.inner.adminAction(action, params, signal);
  }
  manualList(signal?: AbortSignal): Promise<ManualList> {
    return this.inner.manualList(signal);
  }
  manualRegister(record: Record<string, string>, signal?: AbortSignal): Promise<{ ok: boolean; record_id: string }> {
    return this.inner.manualRegister(record, signal);
  }
  manualImport(rows: Array<Record<string, string>>, dryRun: boolean, signal?: AbortSignal): Promise<Record<string, unknown>> {
    return this.inner.manualImport(rows, dryRun, signal);
  }
  manualUndo(recordIds: string[], signal?: AbortSignal): Promise<{ ok: boolean; removed: number }> {
    return this.inner.manualUndo(recordIds, signal);
  }
  private memo<T>(op: string, key: string, fetchFn: () => Promise<T>): Promise<T> {
    const cacheKey = `${this.runId}|${op}|${key}`;
    const hit = this.cache.get(cacheKey) as Promise<T> | undefined;
    if (hit) return hit;
    if (this.cache.size >= 256) this.cache.delete(this.cache.keys().next().value as string);
    const pending = fetchFn().catch(error => {
      this.cache.delete(cacheKey);
      throw error;
    });
    this.cache.set(cacheKey, pending);
    return pending;
  }
  entities(q: string, limit?: number, filters?: SearchFilters, signal?: AbortSignal): Promise<SearchResult> {
    if (signal) return this.inner.entities(q, limit, filters, signal);
    return this.memo("entities", `${q}:${limit ?? 50}:${JSON.stringify(filters ?? {})}`, () => this.inner.entities(q, limit, filters));
  }
  entityExplorer(q: string, offset: number, limit: number, sort: EntitySort, filters?: SearchFilters, cursor?: string, signal?: AbortSignal): Promise<EntityExplorerPage> {
    if (signal) return this.inner.entityExplorer(q, offset, limit, sort, filters, cursor, signal);
    return this.memo("entityExplorer", `${q}:${offset}:${limit}:${sort}:${cursor ?? ""}:${JSON.stringify(filters ?? {})}`, () => this.inner.entityExplorer(q, offset, limit, sort, filters, cursor));
  }
  entityGraph(id: string, includePotential = false, signal?: AbortSignal): Promise<EntityGraph> {
    if (signal) return this.inner.entityGraph(id, includePotential, signal);
    return this.memo("entityGraph", `${id}:${includePotential}`, () => this.inner.entityGraph(id, includePotential));
  }
  normalizePreview(text: string, signal?: AbortSignal): Promise<Record<string, string>> {
    if (signal) return this.inner.normalizePreview(text, signal);
    return this.memo("normalize", text, () => this.inner.normalizePreview(text));
  }
  entity(id: string, offset = 0, limit = 25, signal?: AbortSignal): Promise<EntityDetail> {
    if (signal) return this.inner.entity(id, offset, limit, signal);
    return this.memo("entity", `${id}:${offset}:${limit}`, () => this.inner.entity(id, offset, limit));
  }
  health(signal?: AbortSignal): Promise<HealthInfo> {
    return this.inner.health(signal);
  }
  matchSample(signal?: AbortSignal): Promise<{ record: Record<string, string> }> {
    if (signal) return this.inner.matchSample(signal);
    return this.memo("matchSample", "", () => this.inner.matchSample());
  }
  startRecommendation(record: Record<string, string>, thresholds?: { auto: number; review: number }, signal?: AbortSignal): Promise<MatchJob> {
    return this.inner.startRecommendation(record, thresholds, signal);
  }
  recommendation(id: string, signal?: AbortSignal): Promise<MatchJob> {
    return this.inner.recommendation(id, signal);
  }
  review(minP: number, offset: number, limit: number, nayose?: string, signal?: AbortSignal): Promise<ReviewPage> {
    if (signal) return this.inner.review(minP, offset, limit, nayose, signal);
    return this.memo("review", `${minP}:${offset}:${limit}:${nayose ?? ""}`, () => this.inner.review(minP, offset, limit, nayose));
  }
  metrics(signal?: AbortSignal): Promise<MetricsViewData> {
    if (signal) return this.inner.metrics(signal);
    return this.memo("metrics", this.runId, () => this.inner.metrics());
  }
  metricsFor(run: string, signal?: AbortSignal): Promise<MetricsViewData> {
    if (signal) return this.inner.metricsFor(run, signal);
    return this.memo("metrics", run, () => this.inner.metricsFor(run));
  }
  confusion(signal?: AbortSignal): Promise<ConfusionData> {
    if (signal) return this.inner.confusion(signal);
    return this.memo("confusion", "", () => this.inner.confusion());
  }
  compare(l: string, r: string, signal?: AbortSignal): Promise<Record<string, unknown>[]> {
    if (signal) return this.inner.compare(l, r, signal);
    return this.memo("compare", `${l}:${r}`, () => this.inner.compare(l, r));
  }
  recommend(record: Record<string, string>): Promise<RecommendResult> {
    return this.inner.recommend(record);
  }
  loadDecisions(runId?: string, signal?: AbortSignal): Promise<DecisionSnapshot> {
    return this.inner.loadDecisions(runId, signal);
  }
  saveDecisions(
    items: Array<{ left: string; right: string; ts: string; reviewer?: string }>,
    revision: number,
    runId?: string,
    signal?: AbortSignal,
  ): Promise<number> {
    return this.inner.saveDecisions(items, revision, runId, signal);
  }
}
