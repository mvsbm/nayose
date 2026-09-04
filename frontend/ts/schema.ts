// Column contract — mirrored in api/main.py REQUIRED_COLUMNS; a test
// (tests/test_schema_contract.py) asserts the two stay in sync (Rust-style:
// a shape mismatch fails the build/tests, never at render time in a browser).
export const SCHEMA = {
  full_dataset_summary: ["metric", "value"] as const,
  full_dataset_metrics: ["section", "metric", "value"] as const,
  pair_confusion_test: ["metric", "value"] as const,
  query_confusion: ["scenario", "action", "expected_no", "expected_yes"] as const,
  review_queue: ["left_record_id", "right_record_id"] as const,
  canonical_entities: ["nayose_id", "name_kanji", "name_kana", "record_count"] as const,
  record_to_nayose: ["record_id", "nayose_id"] as const,
} as const;

export type SchemaKey = keyof typeof SCHEMA;

export interface QueryRows {
  columns: string[];
  rows: unknown[][];
  row_count: number;
}

export type RowMap = Record<string, unknown>;

export interface RunInfo {
  id: string;
  path: string;
  current: boolean;
  created?: string;
  fingerprint?: string;
}

export interface RecommendResult {
  action: string;
  top_nayose_id: string | null;
  top_probability: number;
  margin: number;
  candidates: Array<Record<string, unknown>>;
  diagnostics?: Record<string, unknown>;
}

export function toRows(data: QueryRows): RowMap[] {
  return data.rows.map((r) => Object.fromEntries(data.columns.map((c, i) => [c, r[i]])));
}

// Runtime shape guard: throws before render if the server's schema drifted
// (defense-in-depth for the SQL strings we can't type-check).
export function expectColumns(rows: RowMap[], ...required: string[]): void {
  const first = rows[0];
  if (first) {
    for (const c of required) {
      if (!(c in first)) throw new Error(`schema mismatch: missing column '${c}'`);
    }
  }
}

export const num = (v: unknown, fallback = 0): number => {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
};

export const str = (v: unknown): string => (v == null ? "" : String(v));