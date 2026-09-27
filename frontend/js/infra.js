import { adaptDatasetRows } from "./dataset-adapter.js";
import { ApiError } from "./api-error.js";
import { fetchJson } from "./http.js";
import { adaptAdminCapability, adaptAdminConfig, adaptDecisionSnapshot, adaptEntityDetail, adaptEntityExplorerPage, adaptEntityGraph, adaptGenerationPreview, adaptHealth, adaptMatchJob, adaptMetrics, adaptRecordObject, adaptReview, adaptRunList, adaptSearchResult, array, finite, invalid, isObject, object, records, } from "./transport-adapters.js";
export class ApiAdapter {
    runId = "";
    run(id) {
        this.runId = id;
    }
    runs() {
        return fetchJson("/api/runs").then(adaptRunList);
    }
    adminCapability() {
        return fetchJson("/api/admin/capable").then(adaptAdminCapability);
    }
    adminConfig(signal) {
        return fetchJson("/api/admin/config", { signal }).then(adaptAdminConfig);
    }
    adminState(signal) {
        return fetchJson("/api/admin/state", { signal }).then(d => {
            const result = object(d, "admin_state_schema_mismatch");
            if (!isObject(result.profile) || !isObject(result.settings) || !Array.isArray(result.runs) || !finite(result.manual_count) || !Array.isArray(result.audit))
                invalid("admin_state_schema_mismatch");
            return result;
        });
    }
    adminGenerationConfig(signal) {
        return fetchJson("/api/admin/generation-config", { signal }).then(d => {
            const result = object(d, "generation_config_schema_mismatch");
            if (!Array.isArray(result.profiles) || !Array.isArray(result.difficulties) || !Array.isArray(result.events) || !Array.isArray(result.channels) || !Array.isArray(result.query_scenarios) || !isObject(result.defaults) || !isObject(result.bounds))
                invalid("generation_config_schema_mismatch");
            return result;
        });
    }
    adminGenerationPreview(controls, signal) {
        return fetchJson("/api/admin/generation-preview", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ controls }),
            signal,
        }).then(adaptGenerationPreview);
    }
    adminGenerations(signal) {
        return fetchJson("/api/admin/generations", { signal }).then(d => records(array(d, "generations_schema_mismatch"), "generations_schema_mismatch").map(item => {
            if (typeof item.id !== "string" || typeof item.created_at !== "string" || typeof item.fingerprint !== "string" || typeof item.current !== "boolean")
                invalid("generations_schema_mismatch");
            return item;
        }));
    }
    adminDatasetSummary(generationId, signal) {
        return fetchJson(`/api/admin/generations/${encodeURIComponent(generationId)}/summary`, { signal }).then(d => {
            const result = object(d, "dataset_summary_schema_mismatch");
            if (typeof result.id !== "string" || typeof result.created_at !== "string" || typeof result.fingerprint !== "string" || !isObject(result.config) || !isObject(result.counts) || !Array.isArray(result.tables))
                invalid("dataset_summary_schema_mismatch");
            return result;
        });
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
    adminStart(action, params, signal) {
        return fetchJson("/api/admin/jobs", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action, params }),
            signal,
        }).then(adaptMatchJob);
    }
    adminJob(id, signal) {
        return fetchJson(`/api/admin/jobs/${encodeURIComponent(id)}`, { signal }).then(adaptMatchJob);
    }
    adminAction(action, params, signal) {
        return fetchJson("/api/admin/actions", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action, params }),
            signal,
        }).then(d => adaptRecordObject(d, "admin_action_schema_mismatch"));
    }
    manualList(signal) {
        return fetchJson("/api/data-entry", { signal }).then(d => {
            const result = object(d, "manual_list_schema_mismatch");
            if (!finite(result.count) || !Array.isArray(result.rows) || records(result.rows, "manual_list_schema_mismatch").some(row => typeof row.record_id !== "string"))
                invalid("manual_list_schema_mismatch");
            return result;
        });
    }
    manualRegister(record, signal) {
        return fetchJson("/api/data-entry/register", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ record }),
            signal,
        }).then(d => {
            const result = object(d, "manual_register_schema_mismatch");
            if (result.ok !== true || typeof result.record_id !== "string")
                invalid("manual_register_schema_mismatch");
            return result;
        });
    }
    manualImport(rows, dryRun, signal) {
        return fetchJson("/api/data-entry/import", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ rows, dry_run: dryRun }),
            signal,
        }).then(d => adaptRecordObject(d, "manual_import_schema_mismatch"));
    }
    manualUndo(recordIds, signal) {
        return fetchJson("/api/data-entry/undo", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ record_ids: recordIds }),
            signal,
        }).then(d => {
            const result = object(d, "manual_undo_schema_mismatch");
            if (result.ok !== true || !finite(result.removed))
                invalid("manual_undo_schema_mismatch");
            return result;
        });
    }
    entities(q, limit, filters, signal) {
        const params = new URLSearchParams({ q, run_id: this.runId });
        if (limit)
            params.set("limit", String(limit));
        if (filters?.party_type)
            params.set("party_type", filters.party_type);
        if (filters?.min_record_count)
            params.set("min_record_count", String(filters.min_record_count));
        return fetchJson(`/api/entities?${params.toString()}`, { signal }).then(adaptSearchResult);
    }
    entityExplorer(q, offset, limit, sort, filters, cursor, signal) {
        const params = new URLSearchParams({ q, run_id: this.runId, offset: String(offset), limit: String(limit), sort });
        if (cursor)
            params.set("cursor", cursor);
        if (filters?.party_type)
            params.set("party_type", filters.party_type);
        if (filters?.min_record_count)
            params.set("min_record_count", String(filters.min_record_count));
        return fetchJson(`/api/entities/explorer?${params.toString()}`, { signal }).then(adaptEntityExplorerPage);
    }
    entityGraph(id, includePotential = false, signal) {
        return fetchJson(`/api/entity/${encodeURIComponent(id)}/graph?run_id=${encodeURIComponent(this.runId)}&include_potential=${includePotential ? "true" : "false"}`, { signal }).then(adaptEntityGraph);
    }
    normalizePreview(text, signal) {
        return fetchJson(`/api/normalize?text=${encodeURIComponent(text)}`, { signal }).then(d => {
            const result = object(d, "normalize_schema_mismatch");
            if (Object.values(result).some(value => typeof value !== "string"))
                invalid("normalize_schema_mismatch");
            return result;
        });
    }
    entity(id, offset = 0, limit = 25, signal) {
        return fetchJson(`/api/entity/${encodeURIComponent(id)}?run_id=${encodeURIComponent(this.runId)}&offset=${offset}&limit=${limit}`, { signal }).then(adaptEntityDetail);
    }
    health(signal) {
        return fetchJson("/api/health/details", { signal }).then(adaptHealth);
    }
    matchSample(signal) {
        return fetchJson(`/api/match-sample?run_id=${encodeURIComponent(this.runId)}`, { signal }).then(d => {
            const result = object(d, "match_sample_schema_mismatch");
            if (!isObject(result.record) || Object.values(result.record).some(value => typeof value !== "string"))
                invalid("match_sample_schema_mismatch");
            return result;
        });
    }
    startRecommendation(record, thresholds, signal) {
        return fetchJson("/api/recommendations", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ run_id: this.runId, record, ...(thresholds ? { thresholds } : {}) }),
            signal,
        }).then(adaptMatchJob);
    }
    recommendation(id, signal) {
        return fetchJson(`/api/recommendations/${encodeURIComponent(id)}`, { signal }).then(adaptMatchJob);
    }
    review(minP, offset, limit, nayose, signal) {
        const n = nayose ? `&nayose=${encodeURIComponent(nayose)}` : "";
        return fetchJson(`/api/review?run_id=${encodeURIComponent(this.runId)}&min_p=${minP}&offset=${offset}&limit=${limit}${n}`, { signal }).then(adaptReview);
    }
    metrics(signal) {
        return this.metricsFor(this.runId, signal);
    }
    metricsFor(run, signal) {
        return fetchJson(`/api/metrics?run_id=${encodeURIComponent(run)}`, { signal }).then(adaptMetrics);
    }
    confusion(signal) {
        return fetchJson(`/api/confusion?run_id=${encodeURIComponent(this.runId)}`, { signal }).then(d => {
            const result = object(d, "confusion_schema_mismatch");
            const matrix = result.matrix;
            const cohorts = result.error_cohorts;
            const outcomes = result.query_outcomes;
            const validRows = (value) => value === undefined || records(array(value, "confusion_schema_mismatch"), "confusion_schema_mismatch").every(row => typeof row.id === "string" && finite(row.count));
            if (!isObject(result.pairs) || Object.values(result.pairs).some(value => !finite(value)) || !Array.isArray(result.scenarios) || result.scenarios.some(item => !isObject(item) || typeof item.scenario !== "string" || !isObject(item.actions) || Object.values(item.actions).some(value => !finite(value))) || !isObject(result.decisions) || !validRows(matrix) || !validRows(cohorts) || !validRows(outcomes))
                invalid("confusion_schema_mismatch");
            return result;
        });
    }
    compare(l, r, signal) {
        return fetchJson(`/api/compare?run_id=${encodeURIComponent(this.runId)}&left=${encodeURIComponent(l)}&right=${encodeURIComponent(r)}`, { signal }).then(d => {
            const result = object(d, "compare_schema_mismatch");
            return records(array(result.rows, "compare_schema_mismatch"), "compare_schema_mismatch");
        });
    }
    recommend(record) {
        return fetchJson("/api/recommend", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ run_id: this.runId, record }),
        }).then(d => {
            const result = object(d, "recommend_schema_mismatch");
            if (typeof result.action !== "string" || !finite(result.top_probability) || !finite(result.margin) || !Array.isArray(result.candidates) || typeof result.reason !== "string" || !isObject(result.diagnostics) || typeof result.scoring_mode !== "string")
                invalid("recommend_schema_mismatch");
            return result;
        });
    }
    loadDecisions(runId = this.runId, signal) {
        return fetchJson(`/api/decisions?run_id=${encodeURIComponent(runId)}`, { signal }).then(adaptDecisionSnapshot);
    }
    saveDecisions(items, revision, runId = this.runId, signal) {
        const reviewer = items.find((i) => i.reviewer)?.reviewer ?? "";
        return fetchJson("/api/decisions", {
            method: "PUT",
            headers: { "Content-Type": "application/json", ...(reviewer ? { "X-Reviewer": reviewer } : {}) },
            body: JSON.stringify({ run_id: runId, items, revision }),
            signal,
        }).then((data) => {
            const detail = data;
            if (typeof detail.revision !== "number")
                throw new ApiError("invalid_decision_revision");
            return detail.revision;
        });
    }
}
export { MemoProxy } from "./memo-proxy.js";
import { MemoProxy } from "./memo-proxy.js";
// Factory: composes the production port. A WASM data adapter can be swapped
// in later by passing it as the transport (OCP: no composition changes needed).
export function createPort(transport) {
    return new MemoProxy(transport ?? new ApiAdapter());
}
