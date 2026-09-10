import { isRouteName } from "./routes.js";
export function parseRoute(hash) {
    const parts = hash.replace(/^#\/?/, "").split("?")[0].split("/").filter((p) => p.length > 0);
    const name = parts[0] && isRouteName(parts[0]) ? parts[0] : "search";
    return { name, args: parts.slice(1) };
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
export async function pollJob(getStatus, initial, timeoutSec = 300, onUpdate, signal) {
    let job = initial;
    const deadline = Date.now() + timeoutSec * 1000;
    onUpdate?.(job);
    while (Date.now() < deadline && !signal?.aborted) {
        if (job.stage === "complete" || job.stage === "failed")
            return job;
        job = await getStatus();
        if (signal?.aborted)
            return job;
        onUpdate?.(job);
        if (job.stage === "complete" || job.stage === "failed")
            return job;
        await waitForPoll(signal);
    }
    return job;
}
function waitForPoll(signal) {
    if (signal?.aborted)
        return Promise.resolve();
    return new Promise((resolve) => {
        let onAbort;
        const finish = () => {
            if (onAbort)
                signal?.removeEventListener("abort", onAbort);
            resolve();
        };
        const timer = window.setTimeout(finish, 1000);
        onAbort = () => {
            window.clearTimeout(timer);
            finish();
        };
        signal?.addEventListener("abort", onAbort, { once: true });
    });
}
