// Column contract — mirrored in api/main.py REQUIRED_COLUMNS; a test
// (tests/test_schema_contract.py) asserts the two stay in sync (Rust-style:
// a shape mismatch fails the build/tests, never at render time in a browser).
export const SCHEMA = {
    full_dataset_summary: ["metric", "value"],
    full_dataset_metrics: ["section", "metric", "value"],
    pair_confusion_test: ["metric", "value"],
    query_confusion: ["scenario", "action", "expected_no", "expected_yes"],
    review_queue: ["left_record_id", "right_record_id"],
    canonical_entities: ["nayose_id", "name_kanji", "name_kana", "record_count"],
    record_to_nayose: ["record_id", "nayose_id"],
};
export function toRows(data) {
    return data.rows.map((r) => Object.fromEntries(data.columns.map((c, i) => [c, r[i]])));
}
// Runtime shape guard: throws before render if the server's schema drifted
// (defense-in-depth for the SQL strings we can't type-check).
export function expectColumns(rows, ...required) {
    const first = rows[0];
    if (first) {
        for (const c of required) {
            if (!(c in first))
                throw new Error(`schema mismatch: missing column '${c}'`);
        }
    }
}
export const num = (v, fallback = 0) => {
    const n = Number(v);
    return Number.isFinite(n) ? n : fallback;
};
export const str = (v) => (v == null ? "" : String(v));
