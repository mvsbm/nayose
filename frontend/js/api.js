// Typed data adapter (swap `query` for DuckDB-WASM by changing one import).
import { toRows } from "./schema.js";
export class ApiError extends Error {
}
const cache = new Map();
export function clearQueryCache() {
    cache.clear();
}
export async function query(sql, runId) {
    const key = `${runId}|${sql}`;
    const hit = cache.get(key);
    if (hit)
        return hit;
    const p = (async () => {
        const res = await fetch("/api/query", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ run_id: runId, sql }),
        });
        if (!res.ok) {
            const detail = await res.json().catch(() => null);
            throw new ApiError((detail && detail.detail) || res.statusText);
        }
        return toRows((await res.json()));
    })();
    cache.set(key, p);
    return p;
}
export async function listRuns() {
    const res = await fetch("/api/runs");
    if (!res.ok)
        throw new ApiError(`/api/runs ${res.status}`);
    return (await res.json());
}
export async function recommend(record, runId) {
    const res = await fetch("/api/recommend", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_id: runId, record }),
    });
    if (!res.ok) {
        const detail = await res.json().catch(() => null);
        throw new ApiError((detail && detail.detail) || res.statusText);
    }
    return (await res.json());
}
