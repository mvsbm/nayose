// Pure core: no DOM, no fetch — unit-testable with node:test.
// Kept side-effect free so "unit" tests are real units (hermetic, deterministic).

export interface Route {
  name: string;
  args: string[];
}

export function parseRoute(hash: string): Route {
  const parts = hash.replace(/^#\/?/, "").split("?")[0]!.split("/").filter((p) => p.length > 0);
  return { name: parts[0] ?? "search", args: parts.slice(1) };
}

export function checkedKey(run: string, left: string, right: string): string {
  return `${run}|${left}|${right}`;
}

const IGNORED_DIFF_FIELDS = new Set(["record_id", "policy_id", "observed_at"]);

export function diffFields(row: Record<string, unknown>): string[] {
  return Object.keys(row).filter((f) => !IGNORED_DIFF_FIELDS.has(f));
}

export function clamp01(v: number): number {
  return Math.min(1, Math.max(0, Number.isFinite(v) ? v : 0));
}

export function asNumber(v: unknown, fallback = 0): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

export function likeEscaped(q: string): string {
  return q.replace(/[%_\\]/g, (c) => "\\" + c);
}

export function satisfiesMinScore(score: unknown, minP: number): boolean {
  return asNumber(score) >= clamp01(minP);
}

export function pick<T extends Record<string, unknown>, K extends keyof T>(
  row: T,
  keys: readonly K[],
): Pick<T, K> {
  const out = {} as Pick<T, K>;
  for (const k of keys) {
    if (k in row) out[k] = row[k];
  }
  return out;
}