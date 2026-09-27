import {
  AdminCapability,
  AdminConfig,
  DecisionSnapshot,
  EntityDetail,
  EntityExplorerPage,
  EntityGraph,
  GenerationPreview,
  HealthInfo,
  MatchJob,
  MetricsViewData,
  ReviewPage,
  RunInfo,
  SearchResult,
} from "./port.js";
import { ApiError } from "./api-error.js";

export function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
export function invalid(code: string): never {
  throw new ApiError(code);
}
export function object(data: unknown, code: string): Record<string, unknown> {
  return isObject(data) ? data : invalid(code);
}
export function array(data: unknown, code: string): unknown[] {
  return Array.isArray(data) ? data : invalid(code);
}
export function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}
export function records(data: unknown[], code: string): Record<string, unknown>[] {
  return data.map(item => isObject(item) ? item : invalid(code));
}
export function adaptRunList(data: unknown): RunInfo[] {
  return records(array(data, "runs_schema_mismatch"), "runs_schema_mismatch").map(run => {
    if (typeof run.id !== "string" || (run.path !== undefined && typeof run.path !== "string")) invalid("runs_schema_mismatch");
    return { ...run, path: typeof run.path === "string" ? run.path : "" } as unknown as RunInfo;
  });
}
export function adaptAdminCapability(data: unknown): AdminCapability {
  const result = object(data, "admin_capability_schema_mismatch");
  if (typeof result.capable !== "boolean") invalid("admin_capability_schema_mismatch");
  return result as unknown as AdminCapability;
}

export function adaptSearchResult(data: unknown): SearchResult {
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

export function adaptEntityDetail(data: unknown): EntityDetail {
  const result = object(data, "entity_schema_mismatch");
  if (!isObject(result.entity) || !Array.isArray(result.members) || records(result.members, "entity_schema_mismatch").some(member => typeof member.record_id !== "string") || !finite(result.total) || !finite(result.offset) || !finite(result.limit) || !isObject(result.comparisons) || typeof result.merge_evidence_available !== "boolean") invalid("entity_schema_mismatch");
  return result as unknown as EntityDetail;
}

export function adaptHealth(data: unknown): HealthInfo {
  const result = object(data, "health_schema_mismatch");
  const resources = result.resources;
  const memory = isObject(resources) ? resources.memory : undefined;
  const telemetry = isObject(resources) ? resources.telemetry : undefined;
  const accelerators = isObject(resources) ? resources.accelerators : undefined;
  const validTelemetry = telemetry === undefined || telemetry === null || (
    isObject(telemetry)
    && ["cpu_usage_percent", "cpu_temperature_celsius", "gpu_usage_percent", "gpu_temperature_celsius", "gpu_memory_total_bytes", "gpu_memory_available_bytes"]
      .every(key => telemetry[key] === null || finite(telemetry[key]))
  );
  const validAccelerators = accelerators === undefined || (
    Array.isArray(accelerators)
    && accelerators.every(item => isObject(item) && typeof item.kind === "string" && typeof item.framework === "string" && typeof item.name === "string" && typeof item.available === "boolean" && (item.total_memory_bytes === null || finite(item.total_memory_bytes)) && (item.available_memory_bytes === null || finite(item.available_memory_bytes)))
  );
  const configuration = result.configuration;
  const validConfiguration = configuration === undefined || (isObject(configuration) && typeof configuration.hash === "string" && finite(configuration.schema_version) && typeof configuration.source === "string" && (configuration.telemetry_interval_seconds === undefined || finite(configuration.telemetry_interval_seconds)));
  const validResources = resources === undefined || (
    isObject(resources)
    && typeof resources.operating_system === "string"
    && typeof resources.machine === "string"
    && finite(resources.cpu_count)
    && finite(resources.available_cpus)
    && isObject(memory)
    && finite(memory.available_bytes)
    && finite(memory.total_bytes)
    && finite(memory.process_rss_bytes)
    && validTelemetry
    && validAccelerators
  );
  if (
    typeof result.status !== "string"
    || (result.run !== undefined && result.run !== null && typeof result.run !== "string")
    || (result.engine_warm !== undefined && typeof result.engine_warm !== "boolean")
    || (result.stats !== undefined && records(array(result.stats, "health_schema_mismatch"), "health_schema_mismatch").some(stat => typeof stat.endpoint !== "string" || !finite(stat.hits) || !finite(stat.total_ms)))
    || !validResources
    || !validConfiguration
  ) invalid("health_schema_mismatch");
  return result as unknown as HealthInfo;
}

export function adaptMatchJob(data: unknown): MatchJob {
  const result = object(data, "job_schema_mismatch");
  const stages = new Set(["queued", "validating", "loading", "retrieving", "scoring", "aggregating", "complete", "failed", "cancelled"]);
  const progress = result.progress;
  const validProgress = progress === undefined || progress === null || (isObject(progress) && (progress.phase === undefined || typeof progress.phase === "string") && (progress.pct === undefined || progress.pct === null || finite(progress.pct)) && (progress.eta_seconds === undefined || progress.eta_seconds === null || finite(progress.eta_seconds)) && (progress.done === undefined || finite(progress.done)) && (progress.total === undefined || finite(progress.total)));
  if (typeof result.id !== "string" || typeof result.run_id !== "string" || typeof result.stage !== "string" || !stages.has(result.stage) || !finite(result.elapsed_ms) || !Array.isArray(result.timeline) || result.timeline.some(item => !isObject(item) || typeof item.stage !== "string" || !finite(item.elapsed_ms)) || !validProgress || (result.error !== null && result.error !== undefined && typeof result.error !== "string") || (result.result !== null && result.result !== undefined && !isObject(result.result))) invalid("job_schema_mismatch");
  return result as unknown as MatchJob;
}

export function adaptMetrics(data: unknown): MetricsViewData {
  const result = object(data, "metrics_schema_mismatch");
  const groups = result.metric_groups;
  const validGroups = groups === undefined || (isObject(groups) && Object.values(groups).every(value => Array.isArray(value) && records(value, "metrics_schema_mismatch").every(metric => typeof metric.id === "string" && finite(metric.value) && (metric.unit === "ratio" || metric.unit === "count"))));
  const assessment = result.assessment;
  const validAssessment = assessment === undefined || (isObject(assessment) && (assessment.status === "ready" || assessment.status === "attention") && Array.isArray(assessment.reasons) && assessment.reasons.every(reason => typeof reason === "string"));
  const outcomes = result.outcomes;
  const validOutcomes = outcomes === undefined || records(array(outcomes, "metrics_schema_mismatch"), "metrics_schema_mismatch").every(item => typeof item.id === "string" && finite(item.count) && finite(item.rate));
  if (!Array.isArray(result.kpis) || result.kpis.some(item => !Array.isArray(item) || item.length !== 2 || typeof item[0] !== "string" || !finite(item[1])) || !isObject(result.extras) || Object.values(result.extras).some(value => !finite(value)) || !validGroups || !validAssessment || !validOutcomes) invalid("metrics_schema_mismatch");
  return result as unknown as MetricsViewData;
}

export function adaptReview(data: unknown): ReviewPage {
  const result = object(data, "review_schema_mismatch");
  if (!finite(result.total) || !Array.isArray(result.rows) || records(result.rows, "review_schema_mismatch").some(row => typeof row.left_record_id !== "string" || typeof row.right_record_id !== "string" || !finite(row.pair_probability)) || !Array.isArray(result.histogram) || result.histogram.some(item => !Array.isArray(item) || item.length !== 2 || !finite(item[0]) || !finite(item[1]))) invalid("review_schema_mismatch");
  return result as unknown as ReviewPage;
}

export function adaptDecisionSnapshot(data: unknown): DecisionSnapshot {
  const result = object(data, "decisions_schema_mismatch");
  if (!Array.isArray(result.items) || records(result.items, "decisions_schema_mismatch").some(item => typeof item.left !== "string" || typeof item.right !== "string") || !finite(result.revision)) invalid("decisions_schema_mismatch");
  return result as unknown as DecisionSnapshot;
}

export function adaptRecordObject(data: unknown, code: string): Record<string, unknown> {
  return object(data, code);
}

export function adaptEntityExplorerPage(data: unknown): EntityExplorerPage {
  if (!isObject(data)) throw new ApiError("entity_explorer_schema_mismatch");
  const page = data as Partial<EntityExplorerPage>;
  const validSorts = new Set(["relevance", "records", "name", "last_seen"]);
  const validModes = new Set(["exact", "hybrid_fallback"]);
  if (!Array.isArray(page.rows) || page.rows.some(row => !isObject(row) || typeof row.nayose_id !== "string" || typeof row.name_kanji !== "string") || typeof page.total !== "number" || typeof page.offset !== "number" || typeof page.limit !== "number" || typeof page.query !== "string" || !validSorts.has(page.sort ?? "") || !validModes.has(page.search_mode ?? "") || typeof page.total_is_exact !== "boolean" || (page.cursor !== undefined && page.cursor !== null && typeof page.cursor !== "string") || (page.next_cursor !== undefined && page.next_cursor !== null && typeof page.next_cursor !== "string") || (page.has_more !== undefined && typeof page.has_more !== "boolean") || !isObject(page.filters) || !isObject(page.query_plan) || !isObject(page.search_trace)) {
    throw new ApiError("entity_explorer_schema_mismatch");
  }
  return page as EntityExplorerPage;
}

export function adaptEntityGraph(data: unknown): EntityGraph {
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

export function adaptAdminConfig(data: unknown): AdminConfig {
  if (!isObject(data) || typeof data.schema_version !== "number" || typeof data.config_hash !== "string" || typeof data.source !== "string" || data.editable !== false || !isObject(data.sections)) {
    throw new ApiError("admin_config_schema_mismatch");
  }
  const sections = data.sections as Record<string, unknown>;
  if (Object.values(sections).some(section => !isObject(section))) throw new ApiError("admin_config_schema_mismatch");
  return data as unknown as AdminConfig;
}

export function adaptGenerationPreview(data: unknown): GenerationPreview {
  if (!data || typeof data !== "object") throw new ApiError("preview_schema_mismatch");
  const preview = data as Partial<GenerationPreview>;
  if (!isObject(preview.samples) || !isObject(preview.samples.average) || !isObject(preview.time_window) || !isObject(preview.dataset_impact) || !isObject(preview.counts) || !Array.isArray(preview.examples) || preview.examples.some(example => !isObject(example)) || !Array.isArray(preview.events) || !Array.isArray(preview.query_mix) || !isObject(preview.missingness) || !Array.isArray(preview.warnings) || !isObject(preview.estimate_note) || !finite(preview.weighted_band_scale)) {
    throw new ApiError("preview_schema_mismatch");
  }
  return preview as GenerationPreview;
}
