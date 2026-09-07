// Pure core: no DOM, no fetch — unit-testable with node:test.
// Kept side-effect free so "unit" tests are real units (hermetic, deterministic).
import type { MatchJob } from "./port.js";

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

export async function pollJob(
  getStatus: () => Promise<MatchJob>,
  initial: MatchJob,
  timeoutSec = 300,
  onUpdate?: (job: MatchJob) => void,
): Promise<MatchJob> {
  let job = initial;
  const deadline = Date.now() + timeoutSec * 1000;
  onUpdate?.(job);
  while (Date.now() < deadline) {
    if (job.stage === "complete" || job.stage === "failed") return job;
    job = await getStatus();
    onUpdate?.(job);
    if (job.stage === "complete" || job.stage === "failed") return job;
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  return job;
}