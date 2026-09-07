export class ApiError extends Error {
}
function adaptSearchResult(data) {
    if (!data || typeof data !== "object" || !Array.isArray(data.rows))
        throw new ApiError("search_schema_mismatch");
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
async function fetchJson(path, init) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    const headers = new Headers(init?.headers ?? {});
    // Bearer adapter: the transport attaches a deployment token when present, so
    // the GUI works behind NAYOSE_API_TOKEN too. The token is stored under
    // "nayose.token" (e.g. set by an authed wrapper page) — views never think
    // about auth; the Facade owns that sharp edge.
    try {
        const token = localStorage.getItem("nayose.token");
        if (token)
            headers.set("Authorization", `Bearer ${token}`);
    }
    catch { /* storage unavailable: request stays unauthenticated */ }
    try {
        const res = await fetch(path, { ...init, headers, signal: controller.signal });
        if (!res.ok) {
            const detail = await res.json().catch(() => null);
            throw new ApiError(typeof detail?.detail === "string" ? detail.detail : `HTTP ${res.status}`);
        }
        return await res.json();
    }
    catch (err) {
        if (controller.signal.aborted)
            throw new ApiError("request_timeout");
        throw err;
    }
    finally {
        clearTimeout(timeout);
    }
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
    adminDatasetRows(generationId, table, query, offset, limit) {
        const params = new URLSearchParams({ table, q: query, offset: String(offset), limit: String(limit) });
        return fetchJson(`/api/admin/generations/${encodeURIComponent(generationId)}/records?${params}`).then(d => d);
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
    normalizePreview(text) {
        return fetchJson(`/api/normalize?text=${encodeURIComponent(text)}`).then(d => d);
    }
    entity(id, offset = 0, limit = 25) {
        return fetchJson(`/api/entity/${encodeURIComponent(id)}?run_id=${encodeURIComponent(this.runId)}&offset=${offset}&limit=${limit}`).then((d) => d);
    }
    health() {
        return fetchJson("/api/health").then(d => d);
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
        return fetchJson(`/api/decisions?run_id=${encodeURIComponent(this.runId)}`).then((d) => d.items);
    }
    async saveDecisions(items) {
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
    adminDatasetRows(generationId, table, query, offset, limit) {
        return this.inner.adminDatasetRows(generationId, table, query, offset, limit);
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
    saveDecisions(items) {
        return this.inner.saveDecisions(items);
    }
}
// Factory: composes the production port. A WASM data adapter can be swapped
// in later by passing it as the transport (OCP: no composition changes needed).
export function createPort(transport) {
    return new MemoProxy(transport ?? new ApiAdapter());
}
