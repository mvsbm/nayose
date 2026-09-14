import { adaptDatasetRows } from "./dataset-adapter.js";
import { ApiError } from "./api-error.js";
import { fetchJson } from "./http.js";
function adaptSearchResult(data) {
    if (!data || typeof data !== "object" || !Array.isArray(data.rows))
        throw new ApiError("search_schema_mismatch");
    return data;
}
function isObject(value) {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
function adaptEntityExplorerPage(data) {
    if (!isObject(data))
        throw new ApiError("entity_explorer_schema_mismatch");
    const page = data;
    const validSorts = new Set(["relevance", "records", "name", "last_seen"]);
    const validModes = new Set(["exact", "hybrid_fallback"]);
    if (!Array.isArray(page.rows) || typeof page.total !== "number" || typeof page.offset !== "number" || typeof page.limit !== "number" || typeof page.query !== "string" || !validSorts.has(page.sort ?? "") || !validModes.has(page.search_mode ?? "") || typeof page.total_is_exact !== "boolean" || (page.cursor !== undefined && page.cursor !== null && typeof page.cursor !== "string") || (page.next_cursor !== undefined && page.next_cursor !== null && typeof page.next_cursor !== "string") || (page.has_more !== undefined && typeof page.has_more !== "boolean") || !isObject(page.filters) || !isObject(page.query_plan) || !isObject(page.search_trace)) {
        throw new ApiError("entity_explorer_schema_mismatch");
    }
    return page;
}
function adaptEntityGraph(data) {
    if (!isObject(data))
        throw new ApiError("entity_graph_schema_mismatch");
    const graph = data;
    const validKinds = new Set(["entity", "record", "policy", "agency", "channel"]);
    const validEdgeKinds = new Set(["observed", "potential"]);
    const validNode = (node) => isObject(node) && typeof node.id === "string" && typeof node.label === "string" && validKinds.has(String(node.kind)) && isObject(node.metadata);
    const validEdge = (edge) => isObject(edge) && typeof edge.source === "string" && typeof edge.target === "string" && typeof edge.relation === "string" && validEdgeKinds.has(String(edge.kind)) && isObject(edge.evidence);
    if (typeof graph.entity_id !== "string" || !Array.isArray(graph.nodes) || !Array.isArray(graph.edges) || graph.nodes.some(node => !validNode(node)) || graph.edges.some(edge => !validEdge(edge)) || typeof graph.truncated !== "boolean" || typeof graph.potential_matches_available !== "boolean") {
        throw new ApiError("entity_graph_schema_mismatch");
    }
    return graph;
}
function adaptAdminConfig(data) {
    if (!isObject(data) || typeof data.schema_version !== "number" || typeof data.config_hash !== "string" || typeof data.source !== "string" || data.editable !== false || !isObject(data.sections)) {
        throw new ApiError("admin_config_schema_mismatch");
    }
    const sections = data.sections;
    if (Object.values(sections).some(section => !isObject(section)))
        throw new ApiError("admin_config_schema_mismatch");
    return data;
}
function adaptGenerationPreview(data) {
    if (!data || typeof data !== "object")
        throw new ApiError("preview_schema_mismatch");
    const preview = data;
    if (!preview.samples || typeof preview.samples !== "object" || !preview.samples.average || !preview.time_window || !preview.dataset_impact || !Array.isArray(preview.examples)) {
        throw new ApiError("preview_schema_mismatch");
    }
    return preview;
}
export class ApiAdapter {
    runId = "";
    run(id) {
        this.runId = id;
    }
    runs() {
        return fetchJson("/api/runs").then(d => d);
    }
    adminCapability() {
        return fetchJson("/api/admin/capable").then(d => d);
    }
    adminConfig() {
        return fetchJson("/api/admin/config").then(adaptAdminConfig);
    }
    adminState() {
        return fetchJson("/api/admin/state").then(d => d);
    }
    adminGenerationConfig() {
        return fetchJson("/api/admin/generation-config").then(d => d);
    }
    adminGenerationPreview(controls) {
        return fetchJson("/api/admin/generation-preview", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ controls }),
        }).then(adaptGenerationPreview);
    }
    adminGenerations() {
        return fetchJson("/api/admin/generations").then(d => d);
    }
    adminDatasetSummary(generationId) {
        return fetchJson(`/api/admin/generations/${encodeURIComponent(generationId)}/summary`).then(d => d);
    }
    adminDatasetRows(generationId, table, query, offset, limit, filters = {}, includeTotal = false, after = "", signal) {
        const params = new URLSearchParams({
            table,
            q: query,
            offset: String(offset),
            limit: String(limit),
            count: String(includeTotal),
        });
        if (after)
            params.set("after", after);
        if (Object.keys(filters).length)
            params.set("filters", JSON.stringify(filters));
        return fetchJson(`/api/admin/generations/${encodeURIComponent(generationId)}/records?${params}`, { signal }).then(adaptDatasetRows);
    }
    adminStart(action, params) {
        return fetchJson("/api/admin/jobs", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action, params }),
        }).then(d => d);
    }
    adminJob(id) {
        return fetchJson(`/api/admin/jobs/${encodeURIComponent(id)}`).then(d => d);
    }
    adminAction(action, params) {
        return fetchJson("/api/admin/actions", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action, params }),
        }).then(d => d);
    }
    manualList() {
        return fetchJson("/api/data-entry").then(d => d);
    }
    manualRegister(record) {
        return fetchJson("/api/data-entry/register", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ record }),
        }).then(d => d);
    }
    manualImport(rows, dryRun) {
        return fetchJson("/api/data-entry/import", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ rows, dry_run: dryRun }),
        }).then(d => d);
    }
    manualUndo(recordIds) {
        return fetchJson("/api/data-entry/undo", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ record_ids: recordIds }),
        }).then(d => d);
    }
    entities(q, limit, filters) {
        const params = new URLSearchParams({ q, run_id: this.runId });
        if (limit)
            params.set("limit", String(limit));
        if (filters?.party_type)
            params.set("party_type", filters.party_type);
        if (filters?.min_record_count)
            params.set("min_record_count", String(filters.min_record_count));
        return fetchJson(`/api/entities?${params.toString()}`).then(adaptSearchResult);
    }
    entityExplorer(q, offset, limit, sort, filters, cursor) {
        const params = new URLSearchParams({ q, run_id: this.runId, offset: String(offset), limit: String(limit), sort });
        if (cursor)
            params.set("cursor", cursor);
        if (filters?.party_type)
            params.set("party_type", filters.party_type);
        if (filters?.min_record_count)
            params.set("min_record_count", String(filters.min_record_count));
        return fetchJson(`/api/entities/explorer?${params.toString()}`).then(adaptEntityExplorerPage);
    }
    entityGraph(id, includePotential = false) {
        return fetchJson(`/api/entity/${encodeURIComponent(id)}/graph?run_id=${encodeURIComponent(this.runId)}&include_potential=${includePotential ? "true" : "false"}`).then(adaptEntityGraph);
    }
    normalizePreview(text) {
        return fetchJson(`/api/normalize?text=${encodeURIComponent(text)}`).then(d => d);
    }
    entity(id, offset = 0, limit = 25) {
        return fetchJson(`/api/entity/${encodeURIComponent(id)}?run_id=${encodeURIComponent(this.runId)}&offset=${offset}&limit=${limit}`).then((d) => d);
    }
    health() {
        return fetchJson("/api/health/details").then(d => d);
    }
    matchSample() {
        return fetchJson(`/api/match-sample?run_id=${encodeURIComponent(this.runId)}`).then(d => d);
    }
    startRecommendation(record, thresholds) {
        return fetchJson("/api/recommendations", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ run_id: this.runId, record, ...(thresholds ? { thresholds } : {}) }),
        }).then(d => d);
    }
    recommendation(id) {
        return fetchJson(`/api/recommendations/${encodeURIComponent(id)}`).then(d => d);
    }
    review(minP, offset, limit, nayose) {
        const n = nayose ? `&nayose=${encodeURIComponent(nayose)}` : "";
        return fetchJson(`/api/review?run_id=${encodeURIComponent(this.runId)}&min_p=${minP}&offset=${offset}&limit=${limit}${n}`).then((d) => d);
    }
    metrics() {
        return this.metricsFor(this.runId);
    }
    metricsFor(run) {
        return fetchJson(`/api/metrics?run_id=${encodeURIComponent(run)}`).then((d) => d);
    }
    confusion() {
        return fetchJson(`/api/confusion?run_id=${encodeURIComponent(this.runId)}`).then((d) => d);
    }
    compare(l, r) {
        return fetchJson(`/api/compare?run_id=${encodeURIComponent(this.runId)}&left=${encodeURIComponent(l)}&right=${encodeURIComponent(r)}`).then((d) => d.rows);
    }
    recommend(record) {
        return fetchJson("/api/recommend", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ run_id: this.runId, record }),
        }).then((d) => d);
    }
    loadDecisions() {
        return fetchJson(`/api/decisions?run_id=${encodeURIComponent(this.runId)}`).then((d) => d);
    }
    saveDecisions(items, revision) {
        const reviewer = items.find((i) => i.reviewer)?.reviewer ?? "";
        return fetchJson("/api/decisions", {
            method: "PUT",
            headers: { "Content-Type": "application/json", ...(reviewer ? { "X-Reviewer": reviewer } : {}) },
            body: JSON.stringify({ run_id: this.runId, items, revision }),
        }).then((data) => {
            const detail = data;
            if (typeof detail.revision !== "number")
                throw new ApiError("invalid_decision_revision");
            return detail.revision;
        });
    }
}
// Memoizes read operations by run, operation, and arguments. run() clears the
// cache; mutations and command-adjacent reads pass through uncached.
export class MemoProxy {
    inner;
    runId = "";
    cache = new Map();
    constructor(inner) {
        this.inner = inner;
        this.inner = inner;
    }
    run(id) {
        this.runId = id;
        this.cache.clear();
        this.inner.run(id);
    }
    runs() {
        return this.memo("runs", "", () => this.inner.runs());
    }
    adminCapability() {
        return this.memo("adminCapability", "", () => this.inner.adminCapability());
    }
    adminConfig() {
        return this.memo("adminConfig", "", () => this.inner.adminConfig());
    }
    adminState() {
        // mutation-adjacent read: actions mutate server state, so always refetch
        return this.inner.adminState();
    }
    adminGenerationConfig() {
        return this.memo("adminGenerationConfig", "", () => this.inner.adminGenerationConfig());
    }
    adminGenerationPreview(controls) {
        return this.inner.adminGenerationPreview(controls);
    }
    adminGenerations() {
        return this.inner.adminGenerations();
    }
    adminDatasetSummary(generationId) {
        return this.inner.adminDatasetSummary(generationId);
    }
    adminDatasetRows(generationId, table, query, offset, limit, filters, includeTotal, after, signal) {
        return this.inner.adminDatasetRows(generationId, table, query, offset, limit, filters, includeTotal, after, signal);
    }
    adminStart(action, params) {
        return this.inner.adminStart(action, params);
    }
    adminJob(id) {
        return this.inner.adminJob(id);
    }
    adminAction(action, params) {
        return this.inner.adminAction(action, params);
    }
    manualList() {
        // mutation-adjacent read (register/import/undo), never cached
        return this.inner.manualList();
    }
    manualRegister(record) {
        return this.inner.manualRegister(record);
    }
    manualImport(rows, dryRun) {
        return this.inner.manualImport(rows, dryRun);
    }
    manualUndo(recordIds) {
        return this.inner.manualUndo(recordIds);
    }
    memo(op, key, fetchFn) {
        const k = `${this.runId}|${op}|${key}`;
        const hit = this.cache.get(k);
        if (hit)
            return hit;
        const p = fetchFn().catch((e) => {
            this.cache.delete(k);
            throw e;
        });
        this.cache.set(k, p);
        return p;
    }
    entities(q, limit, filters) {
        return this.memo("entities", `${q}:${limit ?? 50}:${JSON.stringify(filters ?? {})}`, () => this.inner.entities(q, limit, filters));
    }
    entityExplorer(q, offset, limit, sort, filters, cursor) {
        return this.memo("entityExplorer", `${q}:${offset}:${limit}:${sort}:${cursor ?? ""}:${JSON.stringify(filters ?? {})}`, () => this.inner.entityExplorer(q, offset, limit, sort, filters, cursor));
    }
    entityGraph(id, includePotential = false) {
        return this.memo("entityGraph", `${id}:${includePotential}`, () => this.inner.entityGraph(id, includePotential));
    }
    normalizePreview(text) {
        return this.memo("normalize", text, () => this.inner.normalizePreview(text));
    }
    entity(id, offset = 0, limit = 25) {
        return this.memo("entity", `${id}:${offset}:${limit}`, () => this.inner.entity(id, offset, limit));
    }
    health() {
        return this.memo("health", "", () => this.inner.health());
    }
    matchSample() {
        return this.memo("matchSample", "", () => this.inner.matchSample());
    }
    startRecommendation(record, thresholds) {
        return this.inner.startRecommendation(record, thresholds);
    }
    recommendation(id) {
        return this.inner.recommendation(id);
    }
    review(minP, offset, limit, nayose) {
        return this.memo("review", `${minP}:${offset}:${limit}:${nayose ?? ""}`, () => this.inner.review(minP, offset, limit, nayose));
    }
    metrics() {
        return this.memo("metrics", this.runId, () => this.inner.metrics());
    }
    metricsFor(run) {
        return this.memo("metrics", run, () => this.inner.metricsFor(run));
    }
    confusion() {
        return this.memo("confusion", "", () => this.inner.confusion());
    }
    compare(l, r) {
        return this.memo("compare", `${l}:${r}`, () => this.inner.compare(l, r));
    }
    recommend(record) {
        return this.inner.recommend(record);
    }
    loadDecisions() {
        return this.inner.loadDecisions();
    }
    saveDecisions(items, revision) {
        return this.inner.saveDecisions(items, revision);
    }
}
// Factory: composes the production port. A WASM data adapter can be swapped
// in later by passing it as the transport (OCP: no composition changes needed).
export function createPort(transport) {
    return new MemoProxy(transport ?? new ApiAdapter());
}
