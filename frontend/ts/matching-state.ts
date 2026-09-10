import type { MatchJob, MatchingPort } from "./port.js";

export class MatchingState {
  draft: Record<string, string> = {};
  submitted: Record<string, string> = {};
  sampleId = "";
  thresholds: { auto: number; review: number } | null = null;
  job: MatchJob | null = null;
  error = "";
  submitting = false;
  polling = false;
  paused = false;
  sampling = false;
  private epoch = 0;

  constructor(private readonly changed: () => void) {}

  reset(): void {
    this.epoch++;
    this.draft = {};
    this.submitted = {};
    this.sampleId = "";
    this.thresholds = null;
    this.job = null;
    this.error = "";
    this.submitting = this.polling = this.paused = this.sampling = false;
    this.changed();
  }

  async start(port: MatchingPort): Promise<void> {
    if (this.submitting || this.polling) return;
    const epoch = ++this.epoch;
    this.submitted = { ...this.draft };
    this.job = null;
    this.error = "";
    this.submitting = true;
    this.changed();
    try {
      const job = await port.startRecommendation(this.submitted, this.thresholds ?? undefined);
      if (epoch !== this.epoch) return;
      this.job = job;
    } catch (err) {
      if (epoch === this.epoch) this.error = String((err as Error).message ?? err);
    } finally {
      if (epoch === this.epoch) { this.submitting = false; this.changed(); }
    }
    if (epoch === this.epoch && this.job) await this.poll(port);
  }

  async sample(port: MatchingPort): Promise<Record<string, string> | null> {
    if (this.sampling) return null;
    this.sampling = true;
    this.changed();
    try {
      const { record } = await port.matchSample();
      this.sampleId = String(record.record_id ?? "");
      this.draft = { ...this.draft, ...record };
      return { ...record };
    } finally {
      this.sampling = false;
      this.changed();
    }
  }

  async poll(port: MatchingPort): Promise<void> {
    if (this.polling || !this.job) return;
    const epoch = this.epoch;
    this.error = "";
    this.paused = false;
    this.polling = true;
    this.changed();
    try {
      for (let attempt = 0; attempt < 300; attempt++) {
        if (this.job.stage === "complete" || this.job.stage === "failed") return;
        const job = await port.recommendation(this.job.id);
        if (epoch !== this.epoch) return;
        this.job = job;
        this.changed();
        if (job.stage === "complete" || job.stage === "failed") return;
        await new Promise((resolve) => setTimeout(resolve, 1000));
        if (epoch !== this.epoch) return;
      }
      this.paused = true;
    } catch (err) {
      if (epoch === this.epoch) {
        this.error = String((err as Error).message ?? err);
        this.paused = true;
      }
    } finally {
      if (epoch === this.epoch) { this.polling = false; this.changed(); }
    }
  }
}
