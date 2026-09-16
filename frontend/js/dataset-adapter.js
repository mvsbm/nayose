import { ApiError } from "./api-error.js";
function isObject(value) {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
export const HIDDEN_COLUMNS = new Set([
    "truth_id", "scenario", "split", "record_quality", "transformation", "target_truth_id",
    "value_owner_truth_id", "relation_id", "hard_case", "identity_top1_correct", "expected_no", "expected_yes",
]);
export function isVisibleDatasetColumn(column) {
    return !HIDDEN_COLUMNS.has(column);
}
export function adaptDatasetRows(data) {
    if (!isObject(data))
        throw new ApiError("dataset_rows_schema_mismatch");
    const rows = data.rows;
    if (typeof data.table !== "string" ||
        !Array.isArray(data.columns) ||
        data.columns.some(column => typeof column !== "string") ||
        !Array.isArray(rows) ||
        rows.some(row => !isObject(row)) ||
        typeof data.total !== "number" ||
        typeof data.offset !== "number" ||
        typeof data.limit !== "number" ||
        data.columns.some(column => HIDDEN_COLUMNS.has(column)) ||
        rows.some(row => Object.keys(row).some(column => HIDDEN_COLUMNS.has(column))) ||
        (data.total_exact !== undefined && typeof data.total_exact !== "boolean") ||
        (data.has_more !== undefined && typeof data.has_more !== "boolean") ||
        (data.next_cursor !== undefined && data.next_cursor !== null && typeof data.next_cursor !== "string") ||
        (data.cursor_column !== undefined && typeof data.cursor_column !== "string")) {
        throw new ApiError("dataset_rows_schema_mismatch");
    }
    return data;
}
