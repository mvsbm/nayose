import type { DatasetGeneration, DatasetPort, DatasetRows, DatasetSummary } from "./port.js";
import { isVisibleDatasetColumn } from "./dataset-adapter.js";
import type { Store } from "./store.js";
import { disposable, DisposableStore, type Disposable } from "./lifecycle.js";
import { esc } from "./schema.js";
import { asyncError, asyncIdle, asyncLoading, asyncReady, type AsyncState } from "./async-state.js";
import { formatNumber } from "./shared.js";
import {
  readColumnFilters,
  renderColumnPicker,
  renderDatasetGrid,
} from "./dataset-grid.js";

export type DatasetTable = "reference" | "queries" | "agencies";

type TableDescriptor = DatasetSummary["tables"][number];

export interface DatasetExplorerState {
  generations: DatasetGeneration[];
  selectedGenerationIds: string[];
  generationId: string;
  table: DatasetTable;
  query: string;
  columnFilters: Record<string, string>;
  visibleColumns: string[];
  cursor?: string;
  cursorHistory: Array<string | undefined>;
  offset: number;
  limit: number;
  summary: DatasetSummary | null;
  summaries: Record<string, DatasetSummary>;
  rows: DatasetRows | null;
  request: AsyncState<DatasetRows>;
  open: boolean;
}

const TABLE_LABELS: Record<DatasetTable, string> = {
  reference: "referenceRecords",
  queries: "incomingQueries",
  agencies: "agencies",
};

const TABLES = Object.keys(TABLE_LABELS) as DatasetTable[];

export function createDatasetExplorerState(generations: DatasetGeneration[]): DatasetExplorerState {
  const current = generations.find(generation => generation.current) ?? generations[0];
  return {
    generations,
    selectedGenerationIds: current ? [current.id] : [],
    generationId: current?.id ?? "",
    table: "reference",
    query: "",
    columnFilters: {},
    visibleColumns: [],
    cursor: undefined,
    cursorHistory: [],
    offset: 0,
    limit: 100,
    summary: null,
    summaries: {},
    rows: null,
    request: asyncIdle(),
    open: true,
  };
}

function localizedGeneration(generation: DatasetGeneration, store: Store): string {
  const localized = store.locale === "ja" ? generation.label_ja : generation.label_en;
  return localized || generation.id;
}

function generationDescription(generation: DatasetGeneration, store: Store): string {
  const localized = store.locale === "ja" ? generation.description_ja : generation.description_en;
  return localized || `${generation.id} · ${generation.fingerprint || store.t("noFingerprint")}`;
}

function tableDescriptor(state: DatasetExplorerState): TableDescriptor | undefined {
  return state.summary?.tables.find(table => table.id === state.table);
}

function allColumns(state: DatasetExplorerState): string[] {
  return (tableDescriptor(state)?.columns ?? state.rows?.columns ?? []).filter(isVisibleDatasetColumn);
}

function number(value: number, store: Store): string {
  return formatNumber(value, store.locale);
}


function renderDatasetPicker(state: DatasetExplorerState, store: Store, t: (key: string, values?: Record<string, string | number>) => string): string {
  const items = state.generations.map(generation => {
    const checked = state.selectedGenerationIds.includes(generation.id);
    const current = generation.current ? ` · ${store.t("currentDataset")}` : "";    return `<label class="dataset-picker-choice"><input type="checkbox" data-dataset-select="${esc(generation.id)}" ${checked ? "checked" : ""}><span><strong>${esc(localizedGeneration(generation, store))}${esc(current)}</strong><small>${esc(generationDescription(generation, store))}</small></span></label>`;
  }).join("");
  return `<fieldset class="dataset-picker"><legend>${t("datasetSelection")}</legend><p class="members">${t("datasetSelectionHelp")}</p><div class="dataset-picker-choice-list">${items}</div><output class="members" aria-live="polite">${t("selectedDatasets", { n: state.selectedGenerationIds.length })}</output></fieldset>`;
}

function renderDatasetTabs(state: DatasetExplorerState, store: Store): string {
  const tabs = state.selectedGenerationIds.map(id => {
    const generation = state.generations.find(item => item.id === id);
    if (!generation) return "";
    const selected = id === state.generationId;
    return `<button class="dataset-tabs-tab" id="dataset-tab-${esc(id)}" type="button" role="tab" aria-selected="${selected}" aria-controls="dataset-panel" data-dataset-tab="${esc(id)}">${esc(localizedGeneration(generation, store))}</button>`;
  }).join("");
  return `<div class="dataset-tabs" role="tablist" aria-label="${esc(store.t("activeDataset"))}">${tabs}</div>`;
}


type Translate = (key: string, values?: Record<string, string | number>) => string;

function renderDatasetSummary(state: DatasetExplorerState, store: Store, columns: string[], t: Translate): string {
  const summary = state.summary;
  const descriptor = tableDescriptor(state);
  const generation = state.generations.find(item => item.id === state.generationId) ?? state.generations[0];
  if (!summary || !generation) return "";
  return `<section class="dataset-info"><h3>${esc(localizedGeneration(generation, store))}</h3><p class="members">${esc(generationDescription(generation, store))}</p><div class="kpi"><div class="card"><span class="l">${t("datasetFingerprint")}</span><div class="v">${esc(summary.fingerprint || "—")}</div></div><div class="card"><span class="l">${t("datasetCreated")}</span><div class="v">${esc(summary.created_at || "—")}</div></div><div class="card"><span class="l">${t("datasetTableRows")}</span><div class="v">${number(descriptor?.rows ?? 0, store)}</div></div><div class="card"><span class="l">${t("datasetColumns")}</span><div class="v">${number(columns.length, store)}</div></div></div><p class="members">${esc(descriptor?.description ?? "")}</p></section>`;
}

function renderTableOptions(state: DatasetExplorerState, store: Store): string {
  return TABLES.map(table => {
    const item = state.summary?.tables.find(candidate => candidate.id === table);
    return `<option value="${table}" ${table === state.table ? "selected" : ""}>${esc(item?.label ?? store.t(TABLE_LABELS[table]))}</option>`;
  }).join("");
}

function renderDatasetToolbar(state: DatasetExplorerState, store: Store, t: Translate): string {
  const pending = state.request.kind === "loading";
  return `<div class="toolbar"><label>${t("datasetTable")} <select id="dataset-table">${renderTableOptions(state, store)}</select></label><label class="dataset-grid-search">${t("datasetSearch")} <input id="dataset-filter" type="search" value="${esc(state.query)}" placeholder="${t("datasetSearchPlaceholder")}"></label><button id="dataset-load" class="more" type="button" ${pending ? "disabled" : ""}>${t("inspectDataset")}</button></div>`;
}

function renderDatasetPaging(state: DatasetExplorerState, total: number, exact: boolean, first: number, last: number, t: Translate): string {
  const range = exact ? t("datasetRange", { first, last, total }) : t("datasetRangeUnknown", { first, last });
  const pending = state.request.kind === "loading";
  const nextDisabled = state.rows?.has_more === false || (!state.rows?.has_more && exact && last >= total) || pending;
  return `<div class="toolbar"><span class="members" role="status" aria-live="polite">${range}</span><button id="dataset-prev" class="more" type="button" ${state.offset === 0 || pending ? "disabled" : ""}>${t("previous")}</button><button id="dataset-next" class="more" type="button" ${nextDisabled ? "disabled" : ""}>${t("next")}</button></div>`;
}

export function renderDatasetExplorer(state: DatasetExplorerState, store: Store): string {
  const t: Translate = (key, values = {}) => esc(store.t(key, values));
  if (!state.generations.length) return `<details id="dataset-explorer"><summary>${t("datasetExplorer")}</summary><p class="members">${t("datasetUnavailable")}</p></details>`;
  const columns = allColumns(state);
  const total = state.rows?.total ?? 0;
  const ariaRows = total >= 0 ? total : state.rows?.rows.length ?? 0;
  const first = state.rows?.rows.length ? state.offset + 1 : 0;
  const last = state.rows?.rows.length ? state.offset + state.rows.rows.length : 0;
  const exact = state.rows?.total_exact !== false && total >= 0;
  const pending = state.request.kind === "loading";
  const error = state.request.kind === "error" ? state.request.message : "";
  return `<details id="dataset-explorer"${state.open ? " open" : ""}><summary>${t("datasetExplorer")}</summary><p>${t("datasetExplorerHelp")}</p>${renderDatasetPicker(state, store, t)}${renderDatasetTabs(state, store)}${renderDatasetToolbar(state, store, t)}${renderDatasetSummary(state, store, columns, t)}${renderColumnPicker(state, store, columns, t)}<div id="dataset-panel" role="tabpanel" aria-labelledby="dataset-tab-${esc(state.generationId)}" class="dataset-grid-scroll" tabindex="0" aria-label="${t("datasetRowsCaption", { n: ariaRows })}" aria-busy="${pending}">${renderDatasetGrid({ request: state.request, rows: state.rows, visibleColumns: state.visibleColumns, columnFilters: state.columnFilters }, store, columns, t)}</div>${renderDatasetPaging(state, total, exact, first, last, t)}${error ? `<p class="notice" role="alert">${esc(error)}</p>` : ""}</details>`;
}

function reconcileColumns(state: DatasetExplorerState, columns: string[]): void {
  if (!state.visibleColumns.length) return;
  state.visibleColumns = state.visibleColumns.filter(column => columns.includes(column));
}

async function load(
  state: DatasetExplorerState,
  port: DatasetPort,
  paint: () => void,
  requestId: number,
  currentRequest: () => number,
  signal: AbortSignal,
): Promise<void> {
  if (!state.generationId) return;
  state.request = asyncLoading(String(requestId));
  paint();
  try {
    const generationId = state.generationId;
    const cachedSummary = state.summaries[generationId];
    const summaryPromise: Promise<DatasetSummary> = cachedSummary
      ? Promise.resolve(cachedSummary)
      : port.adminDatasetSummary(generationId, signal);
    const [summary, rows] = await Promise.all([
      summaryPromise,
      port.adminDatasetRows(generationId, state.table, state.query, state.offset, state.limit, state.columnFilters, false, state.cursor, signal),
    ]);
    if (requestId !== currentRequest()) return;
    state.summaries[generationId] = summary;
    state.summary = summary;
    reconcileColumns(state, allColumns(state));
    state.rows = rows;
    state.request = asyncReady(rows);
  } catch (error) {
    if (requestId !== currentRequest()) return;
    state.request = asyncError(String((error as Error).message ?? error), String(requestId));
  } finally {
    if (requestId !== currentRequest()) return;
    if (state.request.kind === "loading") state.request = asyncIdle();
    paint();
  }
}

export function bindDatasetExplorer(
  host: HTMLElement,
  state: DatasetExplorerState,
  port: DatasetPort,
  store: Store,
): Disposable {
  let requestId = 0;
  const timerStore = new DisposableStore();
  let filterTimer: number | undefined;
  let activeController: AbortController | undefined;
  let disposed = false;
  let paintVersion = 0;
  let frame: number | undefined;
  const paint = (): void => {
    if (disposed) return;
    const version = ++paintVersion;
    const details = host.querySelector<HTMLDetailsElement>("#dataset-explorer");
    if (details) state.open = details.open;
    host.innerHTML = renderDatasetExplorer(state, store);
    bind();
    if (frame !== undefined && typeof cancelAnimationFrame === "function") cancelAnimationFrame(frame);
  };
  const reload = (): void => {
    if (disposed) return;
    activeController?.abort();
    activeController = new AbortController();
    const current = ++requestId;
    void load(state, port, paint, current, () => requestId, activeController.signal);
  };
  const selectGeneration = (id: string): void => {
    if (!state.selectedGenerationIds.includes(id)) state.selectedGenerationIds.push(id);
    state.generationId = id;
    state.summary = state.summaries[id] ?? null;
    state.rows = null;
    state.offset = 0;
    state.visibleColumns = [];
    state.cursor = undefined;
    state.cursorHistory = [];
    state.columnFilters = {};
    reload();
  };
  const bind = (): void => {
    host.querySelectorAll<HTMLInputElement>("[data-dataset-select]").forEach(input => input.addEventListener("change", () => {
      const selected = [...host.querySelectorAll<HTMLInputElement>("[data-dataset-select]:checked")].map(item => item.dataset.datasetSelect ?? "").filter(Boolean);
      if (!selected.length) {
        input.checked = true;
        return;
      }
      state.selectedGenerationIds = selected;
      if (!selected.includes(state.generationId)) selectGeneration(selected[0]!);
      else paint();
    }));
    host.querySelectorAll<HTMLButtonElement>("[data-dataset-tab]").forEach(button => button.addEventListener("click", () => selectGeneration(button.dataset.datasetTab ?? "")));
    host.querySelector<HTMLSelectElement>("#dataset-table")?.addEventListener("change", event => {
      state.table = (event.target as HTMLSelectElement).value as DatasetTable;
      state.offset = 0;
      state.rows = null;
      state.visibleColumns = [];
      state.cursor = undefined;
      state.cursorHistory = [];
      state.columnFilters = {};
      reload();
    });
    host.querySelector<HTMLInputElement>("#dataset-filter")?.addEventListener("keydown", event => {
      if (event.key === "Enter") {
        state.query = (event.target as HTMLInputElement).value.trim();
        state.offset = 0;
        state.cursor = undefined;
        state.cursorHistory = [];
        reload();
      }
    });
    host.querySelector<HTMLInputElement>("#dataset-filter")?.addEventListener("input", event => {
      timerStore.clearTimeout(filterTimer);
      filterTimer = timerStore.addTimeout(() => {
        state.query = (event.target as HTMLInputElement).value.trim();
        state.offset = 0;
        state.cursor = undefined;
        state.cursorHistory = [];
        reload();
      }, 260);
    });
    host.querySelector<HTMLButtonElement>("#dataset-load")?.addEventListener("click", () => {
      state.query = host.querySelector<HTMLInputElement>("#dataset-filter")?.value.trim() ?? "";
      state.columnFilters = readColumnFilters(host);
      state.offset = 0;
      state.cursor = undefined;
      state.cursorHistory = [];
      reload();
    });
    host.querySelector<HTMLButtonElement>("#dataset-filter-apply")?.addEventListener("click", () => {
      state.columnFilters = readColumnFilters(host);
      state.offset = 0;
      state.cursor = undefined;
      state.cursorHistory = [];
      reload();
    });
    host.querySelector<HTMLButtonElement>("#dataset-filter-clear")?.addEventListener("click", () => {
      state.columnFilters = {};
      state.offset = 0;
      state.cursor = undefined;
      state.cursorHistory = [];
      reload();
    });
    host.querySelectorAll<HTMLInputElement>("[data-column-visible]").forEach(input => input.addEventListener("change", () => {
      const column = input.dataset.columnVisible ?? "";
      const columns = allColumns(state);
      const visible = state.visibleColumns.length ? new Set(state.visibleColumns) : new Set(columns);
      if (input.checked) visible.add(column);
      else if (visible.size > 1) visible.delete(column);
      state.visibleColumns = columns.filter(item => visible.has(item));
      paint();
    }));
    host.querySelector<HTMLButtonElement>("#dataset-prev")?.addEventListener("click", () => {
      state.cursor = state.cursorHistory.pop();
      state.offset = Math.max(0, state.offset - state.limit);
      reload();
    });
    host.querySelector<HTMLButtonElement>("#dataset-next")?.addEventListener("click", () => {
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
  if (state.generationId && !state.rows && state.request.kind !== "loading") reload();
  return disposable(() => {
    disposed = true;
    requestId += 1;
    activeController?.abort();
    timerStore.dispose();
    if (frame !== undefined && typeof cancelAnimationFrame === "function") cancelAnimationFrame(frame);
  });
}

export async function refreshDatasetGenerations(state: DatasetExplorerState, port: DatasetPort, signal?: AbortSignal): Promise<void> {
  state.generations = await port.adminGenerations(signal);
  const current = state.generations.find(generation => generation.current) ?? state.generations[0];
  const selected = state.selectedGenerationIds.filter(id => state.generations.some(generation => generation.id === id));
  state.selectedGenerationIds = selected.length ? selected : current ? [current.id] : [];
  if (!state.selectedGenerationIds.includes(state.generationId)) state.generationId = state.selectedGenerationIds[0] ?? "";
  state.summary = null;
  state.summaries = {};
  state.rows = null;
  state.request = asyncIdle();
  state.offset = 0;
  state.visibleColumns = [];
  state.cursor = undefined;
  state.cursorHistory = [];
  state.columnFilters = {};
}
