import { disposable } from "./lifecycle.js";
import { esc } from "./schema.js";
import { observeLockedColumns, readColumnFilters, renderColumnPicker, renderDatasetGrid, syncLockedColumns, } from "./dataset-grid.js";
const TABLE_LABELS = {
    reference: "referenceRecords",
    queries: "incomingQueries",
    agencies: "agencies",
};
const TABLES = Object.keys(TABLE_LABELS);
export function createDatasetExplorerState(generations) {
    const current = generations.find(generation => generation.current) ?? generations[0];
    return {
        generations,
        selectedGenerationIds: current ? [current.id] : [],
        generationId: current?.id ?? "",
        table: "reference",
        query: "",
        columnFilters: {},
        visibleColumns: [],
        lockedColumns: [],
        cursor: undefined,
        cursorHistory: [],
        offset: 0,
        limit: 100,
        summary: null,
        summaries: {},
        rows: null,
        pending: false,
        error: "",
        open: true,
    };
}
function localizedGeneration(generation, store) {
    const localized = store.locale === "ja" ? generation.label_ja : generation.label_en;
    return localized || generation.id;
}
function generationDescription(generation, store) {
    const localized = store.locale === "ja" ? generation.description_ja : generation.description_en;
    return localized || `${generation.id} · ${generation.fingerprint || "no fingerprint"}`;
}
function tableDescriptor(state) {
    return state.summary?.tables.find(table => table.id === state.table);
}
function allColumns(state) {
    return tableDescriptor(state)?.columns ?? state.rows?.columns ?? [];
}
function number(value) {
    return value.toLocaleString();
}
function renderDatasetPicker(state, store, t) {
    const items = state.generations.map(generation => {
        const checked = state.selectedGenerationIds.includes(generation.id);
        const current = generation.current ? ` · ${store.t("currentDataset")}` : "";
        return `<label class="dataset-picker-choice"><input type="checkbox" data-dataset-select="${esc(generation.id)}" ${checked ? "checked" : ""}><span><strong>${esc(localizedGeneration(generation, store))}${esc(current)}</strong><small>${esc(generationDescription(generation, store))}</small></span></label>`;
    }).join("");
    return `<fieldset class="dataset-picker"><legend>${t("datasetSelection")}</legend><p class="members">${t("datasetSelectionHelp")}</p><div class="dataset-picker-choice-list">${items}</div><output class="members" aria-live="polite">${t("selectedDatasets", { n: state.selectedGenerationIds.length })}</output></fieldset>`;
}
function renderDatasetTabs(state, store) {
    const tabs = state.selectedGenerationIds.map(id => {
        const generation = state.generations.find(item => item.id === id);
        if (!generation)
            return "";
        const selected = id === state.generationId;
        return `<button class="dataset-tabs-tab" type="button" role="tab" aria-selected="${selected}" data-dataset-tab="${esc(id)}">${esc(localizedGeneration(generation, store))}</button>`;
    }).join("");
    return `<div class="dataset-tabs" role="tablist" aria-label="${esc(store.t("activeDataset"))}">${tabs}</div>`;
}
function renderDatasetSummary(state, store, columns, t) {
    const summary = state.summary;
    const descriptor = tableDescriptor(state);
    const generation = state.generations.find(item => item.id === state.generationId) ?? state.generations[0];
    if (!summary || !generation)
        return "";
    return `<section class="dataset-info"><h3>${esc(localizedGeneration(generation, store))}</h3><p class="members">${esc(generationDescription(generation, store))}</p><div class="kpi"><div class="card"><span class="l">${t("datasetFingerprint")}</span><div class="v">${esc(summary.fingerprint || "—")}</div></div><div class="card"><span class="l">${t("datasetCreated")}</span><div class="v">${esc(summary.created_at || "—")}</div></div><div class="card"><span class="l">${t("datasetTableRows")}</span><div class="v">${number(descriptor?.rows ?? 0)}</div></div><div class="card"><span class="l">${t("datasetColumns")}</span><div class="v">${number(columns.length)}</div></div></div><p class="members">${esc(descriptor?.description ?? "")}</p></section>`;
}
function renderTableOptions(state, store) {
    return TABLES.map(table => {
        const item = state.summary?.tables.find(candidate => candidate.id === table);
        return `<option value="${table}" ${table === state.table ? "selected" : ""}>${esc(item?.label ?? store.t(TABLE_LABELS[table]))}</option>`;
    }).join("");
}
function renderDatasetToolbar(state, store, t) {
    return `<div class="toolbar"><label>${t("datasetTable")} <select id="dataset-table">${renderTableOptions(state, store)}</select></label><label class="dataset-grid-search">${t("datasetSearch")} <input id="dataset-filter" type="search" value="${esc(state.query)}" placeholder="${t("datasetSearchPlaceholder")}"></label><button id="dataset-load" class="more" type="button" ${state.pending ? "disabled" : ""}>${t("inspectDataset")}</button></div>`;
}
function renderDatasetPaging(state, total, exact, first, last, t) {
    const range = exact ? t("datasetRange", { first, last, total }) : t("datasetRangeUnknown", { first, last });
    const nextDisabled = state.rows?.has_more === false || (!state.rows?.has_more && exact && last >= total) || state.pending;
    return `<div class="toolbar"><span class="members" role="status" aria-live="polite">${range}</span><button id="dataset-prev" class="more" type="button" ${state.offset === 0 || state.pending ? "disabled" : ""}>${t("previous")}</button><button id="dataset-next" class="more" type="button" ${nextDisabled ? "disabled" : ""}>${t("next")}</button></div>`;
}
export function renderDatasetExplorer(state, store) {
    const t = (key, values = {}) => esc(store.t(key, values));
    if (!state.generations.length)
        return `<details id="dataset-explorer"><summary>${t("datasetExplorer")}</summary><p class="members">${t("datasetUnavailable")}</p></details>`;
    const columns = allColumns(state);
    const total = state.rows?.total ?? 0;
    const ariaRows = total >= 0 ? total : state.rows?.rows.length ?? 0;
    const first = state.rows?.rows.length ? state.offset + 1 : 0;
    const last = state.rows?.rows.length ? state.offset + state.rows.rows.length : 0;
    const exact = state.rows?.total_exact !== false && total >= 0;
    return `<details id="dataset-explorer"${state.open ? " open" : ""}><summary>${t("datasetExplorer")}</summary><p>${t("datasetExplorerHelp")}</p>${renderDatasetPicker(state, store, t)}${renderDatasetTabs(state, store)}${renderDatasetToolbar(state, store, t)}${renderDatasetSummary(state, store, columns, t)}${renderColumnPicker(state, store, columns, t)}<div class="dataset-grid-scroll" tabindex="0" role="region" aria-label="${t("datasetRowsCaption", { n: ariaRows })}" aria-busy="${state.pending}">${renderDatasetGrid(state, store, columns, t)}</div>${renderDatasetPaging(state, total, exact, first, last, t)}${state.error ? `<p class="notice" role="alert">${esc(state.error)}</p>` : ""}</details>`;
}
function reconcileColumns(state, columns) {
    if (!state.visibleColumns.length)
        return;
    state.visibleColumns = state.visibleColumns.filter(column => columns.includes(column));
    state.lockedColumns = state.lockedColumns.filter(column => state.visibleColumns.includes(column));
}
async function load(state, port, paint, requestId, currentRequest, signal) {
    if (!state.generationId)
        return;
    state.pending = true;
    state.error = "";
    paint();
    try {
        const generationId = state.generationId;
        const cachedSummary = state.summaries[generationId];
        const summaryPromise = cachedSummary
            ? Promise.resolve(cachedSummary)
            : port.adminDatasetSummary(generationId);
        const [summary, rows] = await Promise.all([
            summaryPromise,
            port.adminDatasetRows(generationId, state.table, state.query, state.offset, state.limit, state.columnFilters, false, state.cursor, signal),
        ]);
        if (requestId !== currentRequest())
            return;
        state.summaries[generationId] = summary;
        state.summary = summary;
        reconcileColumns(state, allColumns(state));
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
    let filterTimer;
    let activeController;
    let disposed = false;
    let paintVersion = 0;
    let stopLockedColumnObserver = () => undefined;
    const paint = () => {
        if (disposed)
            return;
        const version = ++paintVersion;
        const details = host.querySelector("#dataset-explorer");
        if (details)
            state.open = details.open;
        host.innerHTML = renderDatasetExplorer(state, store);
        bind();
        stopLockedColumnObserver();
        const sync = () => syncLockedColumns(host, state.lockedColumns);
        const observe = () => {
            if (disposed || version !== paintVersion)
                return;
            sync();
            stopLockedColumnObserver = observeLockedColumns(host, sync);
        };
        if (typeof requestAnimationFrame === "function")
            requestAnimationFrame(observe);
        else
            window.setTimeout(observe, 0);
    };
    const reload = () => {
        if (disposed)
            return;
        activeController?.abort();
        activeController = new AbortController();
        const current = ++requestId;
        void load(state, port, paint, current, () => requestId, activeController.signal);
    };
    const selectGeneration = (id) => {
        if (!state.selectedGenerationIds.includes(id))
            state.selectedGenerationIds.push(id);
        state.generationId = id;
        state.summary = state.summaries[id] ?? null;
        state.rows = null;
        state.offset = 0;
        state.visibleColumns = [];
        state.lockedColumns = [];
        state.cursor = undefined;
        state.cursorHistory = [];
        state.columnFilters = {};
        reload();
    };
    const bind = () => {
        host.querySelectorAll("[data-dataset-select]").forEach(input => input.addEventListener("change", () => {
            const selected = [...host.querySelectorAll("[data-dataset-select]:checked")].map(item => item.dataset.datasetSelect ?? "").filter(Boolean);
            if (!selected.length) {
                input.checked = true;
                return;
            }
            state.selectedGenerationIds = selected;
            if (!selected.includes(state.generationId))
                selectGeneration(selected[0]);
            else
                paint();
        }));
        host.querySelectorAll("[data-dataset-tab]").forEach(button => button.addEventListener("click", () => selectGeneration(button.dataset.datasetTab ?? "")));
        host.querySelector("#dataset-table")?.addEventListener("change", event => {
            state.table = event.target.value;
            state.offset = 0;
            state.rows = null;
            state.visibleColumns = [];
            state.lockedColumns = [];
            state.cursor = undefined;
            state.cursorHistory = [];
            state.columnFilters = {};
            reload();
        });
        host.querySelector("#dataset-filter")?.addEventListener("keydown", event => {
            if (event.key === "Enter") {
                state.query = event.target.value.trim();
                state.offset = 0;
                state.cursor = undefined;
                state.cursorHistory = [];
                reload();
            }
        });
        host.querySelector("#dataset-filter")?.addEventListener("input", event => {
            window.clearTimeout(filterTimer);
            filterTimer = window.setTimeout(() => {
                state.query = event.target.value.trim();
                state.offset = 0;
                state.cursor = undefined;
                state.cursorHistory = [];
                reload();
            }, 260);
        });
        host.querySelector("#dataset-load")?.addEventListener("click", () => {
            state.query = host.querySelector("#dataset-filter")?.value.trim() ?? "";
            state.columnFilters = readColumnFilters(host);
            state.offset = 0;
            state.cursor = undefined;
            state.cursorHistory = [];
            reload();
        });
        host.querySelector("#dataset-filter-apply")?.addEventListener("click", () => {
            state.columnFilters = readColumnFilters(host);
            state.offset = 0;
            state.cursor = undefined;
            state.cursorHistory = [];
            reload();
        });
        host.querySelector("#dataset-filter-clear")?.addEventListener("click", () => {
            state.columnFilters = {};
            state.offset = 0;
            state.cursor = undefined;
            state.cursorHistory = [];
            reload();
        });
        host.querySelectorAll("[data-column-visible]").forEach(input => input.addEventListener("change", () => {
            const column = input.dataset.columnVisible ?? "";
            const columns = allColumns(state);
            const visible = state.visibleColumns.length ? new Set(state.visibleColumns) : new Set(columns);
            if (input.checked)
                visible.add(column);
            else if (visible.size > 1)
                visible.delete(column);
            state.visibleColumns = columns.filter(item => visible.has(item));
            state.lockedColumns = state.lockedColumns.filter(item => state.visibleColumns.includes(item));
            paint();
        }));
        host.querySelectorAll("[data-column-lock]").forEach(input => input.addEventListener("change", () => {
            const column = input.dataset.columnLock ?? "";
            state.lockedColumns = input.checked
                ? [...state.lockedColumns.filter(item => item !== column), column]
                : state.lockedColumns.filter(item => item !== column);
            paint();
        }));
        host.querySelector("#dataset-prev")?.addEventListener("click", () => {
            state.cursor = state.cursorHistory.pop();
            state.offset = Math.max(0, state.offset - state.limit);
            reload();
        });
        host.querySelector("#dataset-next")?.addEventListener("click", () => {
            const next = state.rows?.next_cursor;
            if (next) {
                state.cursorHistory.push(state.cursor);
                state.cursor = next;
            }
            state.offset += state.limit;
            reload();
        });
    };
    bind();
    if (state.generationId && !state.rows && !state.pending)
        reload();
    return disposable(() => {
        disposed = true;
        requestId += 1;
        activeController?.abort();
        window.clearTimeout(filterTimer);
        stopLockedColumnObserver();
    });
}
export async function refreshDatasetGenerations(state, port) {
    state.generations = await port.adminGenerations();
    const current = state.generations.find(generation => generation.current) ?? state.generations[0];
    const selected = state.selectedGenerationIds.filter(id => state.generations.some(generation => generation.id === id));
    state.selectedGenerationIds = selected.length ? selected : current ? [current.id] : [];
    if (!state.selectedGenerationIds.includes(state.generationId))
        state.generationId = state.selectedGenerationIds[0] ?? "";
    state.summary = null;
    state.summaries = {};
    state.rows = null;
    state.offset = 0;
    state.visibleColumns = [];
    state.lockedColumns = [];
    state.cursor = undefined;
    state.cursorHistory = [];
    state.columnFilters = {};
}
