import type { MatchJob } from "./port.js";
import type { Store } from "./store.js";
import { esc } from "./schema.js";
import { formatNumber } from "./shared.js";

export interface JobStatus {
  job?: MatchJob;
  message?: string;
}

export function renderJobStatus(status: JobStatus, store: Store): string {
  const job = status.job;
  if (job?.stage === "complete") return `${esc(store.t("jobDone"))} · ${esc(store.t("elapsed", { s: (job.elapsed_ms / 1000).toFixed(1) }))}`;
  if (job?.stage === "failed" || job?.stage === "cancelled") return esc(store.t(job.error ?? job.stage));
  const progress = job?.progress;
  if (progress?.pct != null) {
    const pct = Math.min(100, Math.max(0, progress.pct));
    const eta = progress.eta_seconds ?? 0;
    const mmss = `${Math.floor(eta / 60)}:${String(eta % 60).padStart(2, "0")}`;
    const done = progress.done == null ? "" : formatNumber(progress.done, store.locale);
    const total = progress.total == null ? "" : formatNumber(progress.total, store.locale);
    return `<progress class="meter" max="100" value="${pct}" aria-label="${esc(store.t("jobProgress"))}">${esc(Math.round(pct))}%</progress> ${esc(store.t("phase_" + (progress.phase ?? "generating")))} ${esc(Math.round(pct))}% · ${esc(store.t("etaShort", { t: mmss }))} · ${esc(done)}/${esc(total)}`;
  }
  if (status.message) return esc(status.message);
  if (status.job) return `${esc(store.t(status.job.stage))} · ${esc(store.t("elapsed", { s: (status.job.elapsed_ms / 1000).toFixed(1) }))}`;
  return "";
}
