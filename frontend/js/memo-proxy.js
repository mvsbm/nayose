export class MemoProxy {
    inner;
    runId = "";
    cache = new Map();
    constructor(inner) {
        this.inner = inner;
    }
    run(id) {
        this.runId = id;
        this.cache.clear();
        this.inner.run(id);
    }
    runs() {
        return this.inner.runs();
    }
    adminCapability() {
        return this.memo("adminCapability", "", () => this.inner.adminCapability());
    }
    adminConfig(signal) {
        if (signal)
            return this.inner.adminConfig(signal);
        return this.memo("adminConfig", "", () => this.inner.adminConfig());
    }
    adminState(signal) {
        return this.inner.adminState(signal);
    }
    adminGenerationConfig(signal) {
        if (signal)
            return this.inner.adminGenerationConfig(signal);
        return this.memo("adminGenerationConfig", "", () => this.inner.adminGenerationConfig());
    }
    adminGenerationPreview(controls, signal) {
        return this.inner.adminGenerationPreview(controls, signal);
    }
    adminGenerations(signal) {
        return this.inner.adminGenerations(signal);
    }
    adminDatasetSummary(generationId, signal) {
        return this.inner.adminDatasetSummary(generationId, signal);
    }
    adminDatasetRows(generationId, table, query, offset, limit, filters, includeTotal, after, signal) {
        return this.inner.adminDatasetRows(generationId, table, query, offset, limit, filters, includeTotal, after, signal);
    }
    adminStart(action, params, signal) {
        return this.inner.adminStart(action, params, signal);
    }
    adminJob(id, signal) {
        return this.inner.adminJob(id, signal);
    }
    adminAction(action, params, signal) {
        return this.inner.adminAction(action, params, signal);
    }
    manualList(signal) {
        return this.inner.manualList(signal);
    }
    manualRegister(record, signal) {
        return this.inner.manualRegister(record, signal);
    }
    manualImport(rows, dryRun, signal) {
        return this.inner.manualImport(rows, dryRun, signal);
    }
    manualUndo(recordIds, signal) {
        return this.inner.manualUndo(recordIds, signal);
    }
    memo(op, key, fetchFn) {
        const cacheKey = `${this.runId}|${op}|${key}`;
        const hit = this.cache.get(cacheKey);
        if (hit)
            return hit;
        if (this.cache.size >= 256)
            this.cache.delete(this.cache.keys().next().value);
        const pending = fetchFn().catch(error => {
            this.cache.delete(cacheKey);
            throw error;
        });
        this.cache.set(cacheKey, pending);
        return pending;
    }
    entities(q, limit, filters, signal) {
        if (signal)
            return this.inner.entities(q, limit, filters, signal);
        return this.memo("entities", `${q}:${limit ?? 50}:${JSON.stringify(filters ?? {})}`, () => this.inner.entities(q, limit, filters));
    }
    entityExplorer(q, offset, limit, sort, filters, cursor, signal) {
        if (signal)
            return this.inner.entityExplorer(q, offset, limit, sort, filters, cursor, signal);
        return this.memo("entityExplorer", `${q}:${offset}:${limit}:${sort}:${cursor ?? ""}:${JSON.stringify(filters ?? {})}`, () => this.inner.entityExplorer(q, offset, limit, sort, filters, cursor));
    }
    entityGraph(id, includePotential = false, signal) {
        if (signal)
            return this.inner.entityGraph(id, includePotential, signal);
        return this.memo("entityGraph", `${id}:${includePotential}`, () => this.inner.entityGraph(id, includePotential));
    }
    normalizePreview(text, signal) {
        if (signal)
            return this.inner.normalizePreview(text, signal);
        return this.memo("normalize", text, () => this.inner.normalizePreview(text));
    }
    entity(id, offset = 0, limit = 25, signal) {
        if (signal)
            return this.inner.entity(id, offset, limit, signal);
        return this.memo("entity", `${id}:${offset}:${limit}`, () => this.inner.entity(id, offset, limit));
    }
    health(signal) {
        return this.inner.health(signal);
    }
    matchSample(signal) {
        if (signal)
            return this.inner.matchSample(signal);
        return this.memo("matchSample", "", () => this.inner.matchSample());
    }
    startRecommendation(record, thresholds, signal) {
        return this.inner.startRecommendation(record, thresholds, signal);
    }
    recommendation(id, signal) {
        return this.inner.recommendation(id, signal);
    }
    review(minP, offset, limit, nayose, signal) {
        if (signal)
            return this.inner.review(minP, offset, limit, nayose, signal);
        return this.memo("review", `${minP}:${offset}:${limit}:${nayose ?? ""}`, () => this.inner.review(minP, offset, limit, nayose));
    }
    metrics(signal) {
        if (signal)
            return this.inner.metrics(signal);
        return this.memo("metrics", this.runId, () => this.inner.metrics());
    }
    metricsFor(run, signal) {
        if (signal)
            return this.inner.metricsFor(run, signal);
        return this.memo("metrics", run, () => this.inner.metricsFor(run));
    }
    confusion(signal) {
        if (signal)
            return this.inner.confusion(signal);
        return this.memo("confusion", "", () => this.inner.confusion());
    }
    compare(l, r, signal) {
        if (signal)
            return this.inner.compare(l, r, signal);
        return this.memo("compare", `${l}:${r}`, () => this.inner.compare(l, r));
    }
    recommend(record) {
        return this.inner.recommend(record);
    }
    loadDecisions(runId, signal) {
        return this.inner.loadDecisions(runId, signal);
    }
    saveDecisions(items, revision, runId, signal) {
        return this.inner.saveDecisions(items, revision, runId, signal);
    }
}
