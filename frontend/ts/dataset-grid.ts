import type { DatasetRows } from "./port.js";
import type { Store } from "./store.js";
import { esc } from "./schema.js";

export interface DatasetGridModel {
  rows: DatasetRows | null;
  pending: boolean;
  visibleColumns: string[];
  lockedColumns: string[];
  columnFilters: Record<string, string>;
}

type Translate = (key: string, values?: Record<string, string | number>) => string;

function value(raw: unknown): string {
  if (raw == null) return "";
  return typeof raw === "object" ? JSON.stringify(raw) : String(raw);
}

export function displayColumns(model: DatasetGridModel, columns: string[]): string[] {
  const visible = model.visibleColumns.length
    ? columns.filter(column => model.visibleColumns.includes(column))
    : columns;
  const locked = model.lockedColumns.filter(column => visible.includes(column));
  return [...locked, ...visible.filter(column => !locked.includes(column))];
}

export function readColumnFilters(host: HTMLElement): Record<string, string> {
  return Object.fromEntries(
    [...host.querySelectorAll<HTMLInputElement>("[data-column-filter]")]
      .map(input => [input.dataset.columnFilter ?? "", input.value.trim()])
      .filter(([column, filter]) => Boolean(column && filter)),
  );
}

export function renderColumnPicker(
  model: DatasetGridModel,
  store: Store,
  columns: string[],
  t: Translate,
): string {
  if (!columns.length) return "";
  const visible = model.visibleColumns.length ? model.visibleColumns : columns;
  const locked = new Set(model.lockedColumns);
  const items = columns
    .map(
      column =>
        `<div class="dataset-column-picker-choice"><label><input type="checkbox" data-column-visible="${esc(column)}" ${visible.includes(column) ? "checked" : ""}> ${t("visibleColumn")}</label><label><input type="checkbox" data-column-lock="${esc(column)}" ${locked.has(column) ? "checked" : ""} ${visible.includes(column) ? "" : "disabled"}> ${t("lockedColumn")}</label><span>${esc(store.t(column))}</span></div>`,
    )
    .join("");
  return `<details class="dataset-column-picker"><summary>${t("datasetColumnsPicker")}</summary><div class="dataset-column-picker-list">${items}</div><div class="toolbar"><button id="dataset-filter-apply" class="more" type="button">${t("applyColumnFilters")}</button><button id="dataset-filter-clear" class="more" type="button">${t("clearColumnFilters")}</button></div></details>`;
}

export function renderDatasetGrid(
  model: DatasetGridModel,
  store: Store,
  columns: string[],
  t: Translate,
): string {
  const visibleColumns = displayColumns(model, columns);
  if (!model.rows?.rows.length) return `<p class="members">${model.pending ? t("datasetLoading") : t("datasetNoRows")}</p>`;
  const locked = new Set(model.lockedColumns);
  const head = visibleColumns
    .map(
      column =>
        `<th scope="col" data-column="${esc(column)}" class="dataset-grid-head ${locked.has(column) ? "dataset-grid-column-locked" : ""}"><span>${esc(store.t(column))}</span><input type="search" data-column-filter="${esc(column)}" value="${esc(model.columnFilters[column] ?? "")}" placeholder="${t("datasetFilterPlaceholder")}" aria-label="${esc(store.t(column))} ${t("datasetFilterPlaceholder")}"></th>`,
    )
    .join("");
  const body = model.rows.rows
    .map(
      row =>
        `<tr>${visibleColumns.map(column => `<td data-column="${esc(column)}" class="${locked.has(column) ? "dataset-grid-column-locked" : ""}">${esc(value(row[column]))}</td>`).join("")}</tr>`,
    )
    .join("");
  const total = model.rows.total >= 0 ? model.rows.total : model.rows.rows.length;
  return `<table class="dataset-grid"><caption>${t("datasetRowsCaption", { n: total })}</caption><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

export function syncLockedColumns(host: HTMLElement, lockedColumns: string[]): void {
  const locked = new Set(lockedColumns);
  let left = 0;
  for (const column of lockedColumns) {
    const header = [...host.querySelectorAll<HTMLElement>("th[data-column]")].find(node => node.dataset.column === column);
    if (!header) continue;
    host.querySelectorAll<HTMLElement>("[data-column]").forEach(node => {
      if (node.dataset.column !== column) return;
      node.classList.add("dataset-grid-column-locked");
      node.style.left = `${left}px`;
    });
    left += header.getBoundingClientRect().width;
  }
  host.querySelectorAll<HTMLElement>("[data-column]").forEach(node => {
    if (locked.has(node.dataset.column ?? "")) return;
    node.classList.remove("dataset-grid-column-locked");
    node.style.left = "";
  });
}

export function observeLockedColumns(
  host: HTMLElement,
  onResize: () => void,
): () => void {
  if (typeof ResizeObserver === "undefined") return () => undefined;
  const observer = new ResizeObserver(onResize);
  host.querySelectorAll<HTMLElement>("th[data-column]").forEach(header => observer.observe(header));
  return () => observer.disconnect();
}
