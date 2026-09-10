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
    const locked = model.lockedColumns.filter(column => visible.includes(column));
    return [...locked, ...visible.filter(column => !locked.includes(column))];
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
    const locked = new Set(model.lockedColumns);
    const items = columns
        .map(column => `<div class="dataset-column-picker-choice"><label><input type="checkbox" data-column-visible="${esc(column)}" ${visible.includes(column) ? "checked" : ""}> ${t("visibleColumn")}</label><label><input type="checkbox" data-column-lock="${esc(column)}" ${locked.has(column) ? "checked" : ""} ${visible.includes(column) ? "" : "disabled"}> ${t("lockedColumn")}</label><span>${esc(store.t(column))}</span></div>`)
        .join("");
    return `<details class="dataset-column-picker"><summary>${t("datasetColumnsPicker")}</summary><div class="dataset-column-picker-list">${items}</div><div class="toolbar"><button id="dataset-filter-apply" class="more" type="button">${t("applyColumnFilters")}</button><button id="dataset-filter-clear" class="more" type="button">${t("clearColumnFilters")}</button></div></details>`;
}
export function renderDatasetGrid(model, store, columns, t) {
    const visibleColumns = displayColumns(model, columns);
    if (!model.rows?.rows.length)
        return `<p class="members">${model.pending ? t("datasetLoading") : t("datasetNoRows")}</p>`;
    const locked = new Set(model.lockedColumns);
    const head = visibleColumns
        .map(column => `<th scope="col" data-column="${esc(column)}" class="dataset-grid-head ${locked.has(column) ? "dataset-grid-column-locked" : ""}"><span>${esc(store.t(column))}</span><input type="search" data-column-filter="${esc(column)}" value="${esc(model.columnFilters[column] ?? "")}" placeholder="${t("datasetFilterPlaceholder")}" aria-label="${esc(store.t(column))} ${t("datasetFilterPlaceholder")}"></th>`)
        .join("");
    const body = model.rows.rows
        .map(row => `<tr>${visibleColumns.map(column => `<td data-column="${esc(column)}" class="${locked.has(column) ? "dataset-grid-column-locked" : ""}">${esc(value(row[column]))}</td>`).join("")}</tr>`)
        .join("");
    const total = model.rows.total >= 0 ? model.rows.total : model.rows.rows.length;
    return `<table class="dataset-grid"><caption>${t("datasetRowsCaption", { n: total })}</caption><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}
export function syncLockedColumns(host, lockedColumns) {
    const locked = new Set(lockedColumns);
    let left = 0;
    for (const column of lockedColumns) {
        const header = [...host.querySelectorAll("th[data-column]")].find(node => node.dataset.column === column);
        if (!header)
            continue;
        host.querySelectorAll("[data-column]").forEach(node => {
            if (node.dataset.column !== column)
                return;
            node.classList.add("dataset-grid-column-locked");
            node.style.left = `${left}px`;
        });
        left += header.getBoundingClientRect().width;
    }
    host.querySelectorAll("[data-column]").forEach(node => {
        if (locked.has(node.dataset.column ?? ""))
            return;
        node.classList.remove("dataset-grid-column-locked");
        node.style.left = "";
    });
}
export function observeLockedColumns(host, onResize) {
    if (typeof ResizeObserver === "undefined")
        return () => undefined;
    const observer = new ResizeObserver(onResize);
    host.querySelectorAll("th[data-column]").forEach(header => observer.observe(header));
    return () => observer.disconnect();
}
