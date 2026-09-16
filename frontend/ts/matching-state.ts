import type { MatchJob, MatchingPort } from "./port.js";
import { asyncError, asyncIdle, asyncLoading, asyncReady, type AsyncState } from "./async-state.js";

export class MatchingState {
  draft: Record<string, string> = {};
  submitted: Record<string, string> = {};
  sampleId = "";
  thresholds: { auto: number; review: number } | null = null;
  job: MatchJob | null = null;
  paused = false;
  private epoch = 0;
  private controller: AbortController | undefined;
  private submissionRequest: AsyncState<MatchJob> = asyncIdle();
  private pollingRequest: AsyncState<MatchJob> = asyncIdle();
  private sampleRequest: AsyncState<Record<string, string>> = asyncIdle();

  constructor(private readonly changed: () => void) {}

  get submitting(): boolean {
    return this.submissionRequest.kind === "loading";
  }

  get polling(): boolean {
    return this.pollingRequest.kind === "loading";
  }

  get sampling(): boolean {
    return this.sampleRequest.kind === "loading";
  }

  get error(): string {
    for (const request of [this.submissionRequest, this.pollingRequest, this.sampleRequest]) {
      if (request.kind === "error") return request.message;
    }
    return "";
  }

  private clearErrors(): void {
    if (this.submissionRequest.kind === "error") this.submissionRequest = asyncIdle();
    if (this.pollingRequest.kind === "error") this.pollingRequest = asyncIdle();
    if (this.sampleRequest.kind === "error") this.sampleRequest = asyncIdle();
  }

  private stopLoading(): void {
    if (this.submissionRequest.kind === "loading") this.submissionRequest = asyncIdle();
    if (this.pollingRequest.kind === "loading") this.pollingRequest = asyncIdle();
    if (this.sampleRequest.kind === "loading") this.sampleRequest = asyncIdle();
  }

  cancel(): void {
    this.epoch++;
    this.controller?.abort();
    this.controller = undefined;
    this.stopLoading();
    this.changed();
  }

  reset(): void {
    this.epoch++;
    this.controller?.abort();
    this.controller = undefined;
    this.draft = {};
    this.submitted = {};
    this.sampleId = "";
    this.thresholds = null;
    this.job = null;
    this.submissionRequest = asyncIdle();
    this.pollingRequest = asyncIdle();
    this.sampleRequest = asyncIdle();
    this.paused = false;
    this.changed();
  }

  async start(port: MatchingPort): Promise<void> {
    if (this.submitting || this.polling) return;
    const epoch = ++this.epoch;
    this.controller?.abort();
    const controller = new AbortController();
    this.controller = controller;
    this.submitted = { ...this.draft };
    this.job = null;
    this.clearErrors();
    this.submissionRequest = asyncLoading(String(epoch));
    this.changed();
    try {
      const job = await port.startRecommendation(this.submitted, this.thresholds ?? undefined, controller.signal);
      if (epoch !== this.epoch) return;
      this.job = job;
      this.submissionRequest = asyncReady(job);
    } catch (err) {
      if (epoch === this.epoch) this.submissionRequest = asyncError(String((err as Error).message ?? err), String(epoch));
    } finally {
      if (epoch === this.epoch) {
        if (this.submissionRequest.kind === "loading") this.submissionRequest = asyncIdle();
        this.changed();
      }
    }
    if (epoch === this.epoch && this.job) await this.poll(port, controller.signal);
    if (this.controller === controller) this.controller = undefined;
  }

  async sample(port: MatchingPort): Promise<Record<string, string> | null> {
    if (this.sampling) return null;
    this.clearErrors();
    this.sampleRequest = asyncLoading(`sample-${this.epoch}`);
    const controller = new AbortController();
    this.controller?.abort();
    this.controller = controller;
    this.changed();
    try {
      const { record } = await port.matchSample(controller.signal);
      this.sampleId = String(record.record_id ?? "");
      this.draft = { ...this.draft, ...record };
      this.sampleRequest = asyncReady(record);
      return { ...record };
    } catch (error) {
      this.sampleRequest = asyncError(String((error as Error).message ?? error), `sample-${this.epoch}`);
      throw error;
    } finally {
      if (this.sampleRequest.kind === "loading") this.sampleRequest = asyncIdle();
      if (this.controller === controller) this.controller = undefined;
      this.changed();
    }
  }

  async poll(port: MatchingPort, signal?: AbortSignal): Promise<void> {
    if (this.polling || !this.job) return;
    const epoch = this.epoch;
    this.clearErrors();
    this.paused = false;
    this.pollingRequest = asyncLoading(`poll-${epoch}`);
    this.changed();
    try {
      for (let attempt = 0; attempt < 300; attempt++) {
        if (this.job.stage === "complete" || this.job.stage === "failed" || this.job.stage === "cancelled") {
          this.pollingRequest = asyncReady(this.job);
          return;
        }
        const job = await port.recommendation(this.job.id, signal);
        if (epoch !== this.epoch) return;
        this.job = job;
        this.changed();
        if (job.stage === "complete" || job.stage === "failed" || job.stage === "cancelled") {
          this.pollingRequest = asyncReady(job);
          return;
        }
        await new Promise<void>((resolve) => {
          if (signal?.aborted) { resolve(); return; }
          const timer = window.setTimeout(resolve, 1000);
          signal?.addEventListener("abort", () => { window.clearTimeout(timer); resolve(); }, { once: true });
        });
        if (epoch !== this.epoch || signal?.aborted) return;
      }
      this.paused = true;
    } catch (err) {
      if (epoch === this.epoch) {
        this.pollingRequest = asyncError(String((err as Error).message ?? err), `poll-${epoch}`);
        this.paused = true;
      }
    } finally {
      if (epoch === this.epoch) {
        if (this.pollingRequest.kind === "loading") this.pollingRequest = asyncIdle();
        this.changed();
      }
    }
  }
}
