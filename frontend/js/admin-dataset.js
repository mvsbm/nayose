import { esc } from "./schema.js";
const TABLE_LABELS = {
    reference: "referenceRecords",
    queries: "incomingQueries",
    agencies: "agencies",
};
export function createDatasetExplorerState(generations) {
    const current = generations.find(generation => generation.current) ?? generations[0];
    return {
        generations,
        generationId: current?.id ?? "",
        table: "reference",
        query: "",
        offset: 0,
        limit: 25,
        summary: null,
        rows: null,
        pending: false,
        error: "",
        open: true,
    };
}
export function renderDatasetExplorer(state, store) {
    const t = (key, values = {}) => esc(store.t(key, values));
    const generationOptions = state.generations.map(generation => `<option value="${esc(generation.id)}" ${generation.id === state.generationId ? "selected" : ""}>${esc(generation.id)}${generation.current ? " ★" : ""}</option>`).join("");
    const tableOptions = Object.keys(TABLE_LABELS).map(table => `<option value="${table}" ${table === state.table ? "selected" : ""}>${t(TABLE_LABELS[table])}</option>`).join("");
    if (!state.generations.length)
        return `<details id="dataset-explorer"><summary>${t("datasetExplorer")}</summary><p class="members">${t("datasetUnavailable")}</p></details>`;
    const summary = state.summary;
    const tableSummary = summary?.tables.find(table => table.id === state.table);
    const summaryHtml = summary ? `<div class="kpi"><div class="card"><span class="l">${t("datasetFingerprint")}</span><div class="v">${esc(summary.fingerprint || "—")}</div></div><div class="card"><span class="l">${t("datasetCreated")}</span><div class="v">${esc(summary.created_at || "—")}</div></div><div class="card"><span class="l">${t("datasetTableRows")}</span><div class="v">${number(tableSummary?.rows ?? 0)}</div></div><div class="card"><span class="l">${t("datasetColumns")}</span><div class="v">${number(tableSummary?.columns.length ?? 0)}</div></div></div>` : "";
    const rowsHtml = state.rows?.rows.length ? `<table><caption>${t("datasetRowsCaption", { n: state.rows.total })}</caption><thead><tr>${state.rows.columns.map(column => `<th scope="col">${esc(store.t(column))}</th>`).join("")}</tr></thead><tbody>${state.rows.rows.map(row => `<tr>${state.rows.columns.map(column => `<td>${esc(value(row[column]))}</td>`).join("")}</tr>`).join("")}</tbody></table>` : `<p class="members">${state.pending ? t("datasetLoading") : t("datasetNoRows")}</p>`;
    const total = state.rows?.total ?? 0;
    const first = total ? state.offset + 1 : 0;
    const last = Math.min(state.offset + state.limit, total);
    return `<details id="dataset-explorer"${state.open ? " open" : ""}><summary>${t("datasetExplorer")}</summary><p>${t("datasetExplorerHelp")}</p><div class="toolbar"><label>${t("generation")} <select id="dataset-generation">${generationOptions}</select></label><label>${t("datasetTable")} <select id="dataset-table">${tableOptions}</select></label><label>${t("datasetSearch")} <input id="dataset-filter" type="search" value="${esc(state.query)}" placeholder="${t("datasetSearchPlaceholder")}"></label><button id="dataset-load" class="more" type="button" ${state.pending ? "disabled" : ""}>${t("inspectDataset")}</button></div>${summaryHtml}<div class="table-scroll" tabindex="0" role="region" aria-label="${t("datasetRowsCaption", { n: total })}" aria-busy="${state.pending}">${rowsHtml}</div><div class="toolbar"><span class="members" role="status" aria-live="polite">${t("datasetRange", { first, last, total })}</span><button id="dataset-prev" class="more" type="button" ${state.offset === 0 || state.pending ? "disabled" : ""}>${t("previous")}</button><button id="dataset-next" class="more" type="button" ${last >= total || state.pending ? "disabled" : ""}>${t("next")}</button></div>${state.error ? `<p class="notice" role="alert">${esc(state.error)}</p>` : ""}</details>`;
}
function number(value) {
    return value.toLocaleString();
}
function value(raw) {
    if (raw == null)
        return "";
    return typeof raw === "object" ? JSON.stringify(raw) : String(raw);
}
async function load(state, port, paint, requestId, currentRequest) {
    if (!state.generationId)
        return;
    state.pending = true;
    state.error = "";
    paint();
    try {
        const [summary, rows] = await Promise.all([
            state.summary?.id === state.generationId ? Promise.resolve(state.summary) : port.adminDatasetSummary(state.generationId),
            port.adminDatasetRows(state.generationId, state.table, state.query, state.offset, state.limit),
        ]);
        if (requestId !== currentRequest())
            return;
        state.summary = summary;
        state.rows = rows;
    }
    catch (error) {
        if (requestId !== currentRequest())
            return;
        state.error = String(error.message ?? error);
    }
    finally {
        if (requestId !== currentRequest())
            return;
        state.pending = false;
        paint();
    }
}
export function bindDatasetExplorer(host, state, port, store) {
    let requestId = 0;
    const paint = () => {
        const details = host.querySelector("#dataset-explorer");
        if (details)
            state.open = details.open;
        host.innerHTML = renderDatasetExplorer(state, store);
        bind();
    };
    const reload = () => {
        const current = ++requestId;
        void load(state, port, paint, current, () => requestId);
    };
    const bind = () => {
        host.querySelector("#dataset-explorer")?.addEventListener("toggle", event => {
            state.open = event.target.open;
        });
        host.querySelector("#dataset-generation")?.addEventListener("change", event => {
            state.generationId = event.target.value;
            state.offset = 0;
            state.summary = null;
            state.rows = null;
            reload();
        });
        host.querySelector("#dataset-table")?.addEventListener("change", event => {
            state.table = event.target.value;
            state.offset = 0;
            state.rows = null;
            reload();
        });
        host.querySelector("#dataset-filter")?.addEventListener("keydown", event => {
            if (event.key === "Enter") {
                state.query = event.target.value.trim();
                state.offset = 0;
                reload();
            }
        });
        host.querySelector("#dataset-load")?.addEventListener("click", () => {
            state.query = host.querySelector("#dataset-filter")?.value.trim() ?? "";
            state.offset = 0;
            reload();
        });
        host.querySelector("#dataset-prev")?.addEventListener("click", () => {
            state.offset = Math.max(0, state.offset - state.limit);
            reload();
        });
        host.querySelector("#dataset-next")?.addEventListener("click", () => {
            state.offset += state.limit;
            reload();
        });
    };
    bind();
    if (state.generationId && !state.rows && !state.pending)
        reload();
}
export async function refreshDatasetGenerations(state, port) {
    state.generations = await port.adminGenerations();
    const current = state.generations.find(generation => generation.current) ?? state.generations[0];
    if (!state.generations.some(generation => generation.id === state.generationId))
        state.generationId = current?.id ?? "";
    state.summary = null;
    state.rows = null;
    state.offset = 0;
}
