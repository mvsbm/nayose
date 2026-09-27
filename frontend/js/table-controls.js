import { disposable } from "./lifecycle.js";
let tableId = 0;
function cellText(row, column) {
    return row.cells[column]?.textContent?.trim() ?? "";
}
function compareRows(left, right, column, direction) {
    const a = cellText(left, column);
    const b = cellText(right, column);
    const numberA = Number(a.replaceAll(",", ""));
    const numberB = Number(b.replaceAll(",", ""));
    const result = Number.isFinite(numberA) && Number.isFinite(numberB)
        ? numberA - numberB
        : a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
    return direction === "ascending" ? result : -result;
}
function columnLabels(table) {
    const header = table.tHead?.rows[0] ?? table.tBodies[0]?.rows[0];
    return header ? [...header.cells].map((cell, index) => cell.textContent?.trim() || `#${index + 1}`) : [];
}
function enhanceTable(table, store) {
    if (table.dataset.tableEnhanced === "true")
        return;
    table.dataset.tableEnhanced = "true";
    if (!table.id)
        table.id = `table-${++tableId}`;
    const labels = columnLabels(table);
    const tools = document.createElement("div");
    tools.className = "table-tools";
    tools.setAttribute("data-table-tools", table.id);
    const searchLabel = document.createElement("label");
    searchLabel.className = "table-search";
    const searchText = document.createElement("span");
    searchText.textContent = store.t("tableSearch");
    const search = document.createElement("input");
    search.className = "text-input";
    search.type = "search";
    search.placeholder = store.t("tableSearchPlaceholder");
    search.setAttribute("aria-controls", table.id);
    searchLabel.append(searchText, search);
    const sortLabel = document.createElement("label");
    sortLabel.className = "table-tools-sort-field";
    const sortText = document.createElement("span");
    sortText.textContent = store.t("tableSort");
    const select = document.createElement("select");
    select.setAttribute("aria-controls", table.id);
    const noSort = document.createElement("option");
    noSort.value = "";
    noSort.textContent = store.t("tableSortNone");
    select.append(noSort);
    labels.forEach((label, index) => {
        const option = document.createElement("option");
        option.value = String(index);
        option.textContent = label;
        select.append(option);
    });
    sortLabel.append(sortText, select);
    const direction = document.createElement("button");
    direction.type = "button";
    direction.className = "button button-toolbar table-tools-sort-button";
    direction.disabled = !labels.length;
    direction.dataset.direction = "ascending";
    direction.textContent = "↑";
    direction.setAttribute("aria-label", store.t("tableAscending"));
    direction.setAttribute("aria-controls", table.id);
    direction.setAttribute("aria-pressed", "false");
    tools.append(searchLabel, sortLabel, direction);
    table.parentElement?.insertBefore(tools, table);
    let query = "";
    let sortColumn;
    let sortDirection = "ascending";
    const rows = () => table.tBodies[0] ? [...table.tBodies[0].rows] : [];
    const applySearch = () => {
        const normalized = query.toLocaleLowerCase();
        rows().forEach(row => { row.hidden = Boolean(normalized && !row.textContent?.toLocaleLowerCase().includes(normalized)); });
    };
    const applySort = () => {
        if (sortColumn === undefined || !table.tBodies[0])
            return;
        rows().sort((left, right) => compareRows(left, right, sortColumn, sortDirection)).forEach(row => table.tBodies[0].append(row));
        applySearch();
    };
    search.addEventListener("input", () => {
        query = search.value.trim();
        applySearch();
    });
    select.addEventListener("change", () => {
        sortColumn = select.value === "" ? undefined : Number(select.value);
        direction.setAttribute("aria-pressed", String(sortColumn !== undefined));
        applySort();
    });
    direction.addEventListener("click", () => {
        sortDirection = sortDirection === "ascending" ? "descending" : "ascending";
        direction.dataset.direction = sortDirection;
        direction.textContent = sortDirection === "ascending" ? "↑" : "↓";
        direction.setAttribute("aria-label", store.t(sortDirection === "ascending" ? "tableAscending" : "tableDescending"));
        applySort();
    });
}
export function enhanceTables(root, store) {
    root.querySelectorAll("table").forEach(table => enhanceTable(table, store));
}
export function observeTables(root, store) {
    enhanceTables(root, store);
    const Observer = root.ownerDocument.defaultView?.MutationObserver;
    if (typeof Observer !== "function")
        return disposable(() => undefined);
    const observer = new Observer(() => enhanceTables(root, store));
    observer.observe(root, { childList: true, subtree: true });
    return disposable(() => observer.disconnect());
}
