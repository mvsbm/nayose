import { esc } from "./schema.js";
function value(raw) {
    if (raw == null)
        return "";
    return typeof raw === "object" ? JSON.stringify(raw) : String(raw);
}
export function displayColumns(model, columns) {
    const visible = model.visibleColumns.length
        ? columns.filter(column => model.visibleColumns.includes(column))
        : columns;
    return visible;
}
export function readColumnFilters(host) {
    return Object.fromEntries([...host.querySelectorAll("[data-column-filter]")]
        .map(input => [input.dataset.columnFilter ?? "", input.value.trim()])
        .filter(([column, filter]) => Boolean(column && filter)));
}
export function renderColumnPicker(model, store, columns, t) {
    if (!columns.length)
        return "";
    const visible = model.visibleColumns.length ? model.visibleColumns : columns;
    const items = columns
        .map(column => `<div class="dataset-column-picker-choice"><label><input type="checkbox" data-column-visible="${esc(column)}" ${visible.includes(column) ? "checked" : ""}> ${t("visibleColumn")} <span>${esc(store.t(column))}</span></label></div>`)
        .join("");
    return `<details class="dataset-column-picker"><summary>${t("datasetColumnsPicker")}</summary><div class="dataset-column-picker-list">${items}</div><div class="toolbar"><button id="dataset-filter-apply" class="button button-secondary" type="button">${t("applyColumnFilters")}</button><button id="dataset-filter-clear" class="button button-secondary" type="button">${t("clearColumnFilters")}</button></div></details>`;
}
export function renderDatasetGrid(model, store, columns, t) {
    const visibleColumns = displayColumns(model, columns);
    if (!model.rows?.rows.length)
        return `<p class="text-meta">${model.request.kind === "loading" ? t("datasetLoading") : t("datasetNoRows")}</p>`;
    const head = visibleColumns
        .map(column => `<th scope="col" data-column="${esc(column)}" class="dataset-grid-head"><span>${esc(store.t(column))}</span><input type="search" data-column-filter="${esc(column)}" value="${esc(model.columnFilters[column] ?? "")}" placeholder="${t("datasetFilterPlaceholder")}" aria-label="${esc(store.t(column))} ${t("datasetFilterPlaceholder")}"></th>`)
        .join("");
    const body = model.rows.rows
        .map(row => `<tr>${visibleColumns.map(column => `<td data-column="${esc(column)}">${esc(value(row[column]))}</td>`).join("")}</tr>`)
        .join("");
    const total = model.rows.total >= 0 ? model.rows.total : model.rows.rows.length;
    return `<table class="dataset-grid"><caption>${t("datasetRowsCaption", { n: total })}</caption><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}
