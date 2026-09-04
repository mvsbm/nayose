// Pure core: no DOM, no fetch — unit-testable with node:test.
// Kept side-effect free so "unit" tests are real units (hermetic, deterministic).
export function parseRoute(hash) {
    const parts = hash.replace(/^#\/?/, "").split("?")[0].split("/").filter((p) => p.length > 0);
    return { name: parts[0] ?? "search", args: parts.slice(1) };
}
export function checkedKey(run, left, right) {
    return `${run}|${left}|${right}`;
}
const IGNORED_DIFF_FIELDS = new Set(["record_id", "policy_id", "observed_at"]);
export function diffFields(row) {
    return Object.keys(row).filter((f) => !IGNORED_DIFF_FIELDS.has(f));
}
export function clamp01(v) {
    return Math.min(1, Math.max(0, Number.isFinite(v) ? v : 0));
}
export function asNumber(v, fallback = 0) {
    const n = Number(v);
    return Number.isFinite(n) ? n : fallback;
}
export function likeEscaped(q) {
    return q.replace(/[%_\\]/g, (c) => "\\" + c);
}
export function satisfiesMinScore(score, minP) {
    return asNumber(score) >= clamp01(minP);
}
export function pick(row, keys) {
    const out = {};
    for (const k of keys) {
        if (k in row)
            out[k] = row[k];
    }
    return out;
}
