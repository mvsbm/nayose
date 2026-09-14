export class MatchingState {
    changed;
    draft = {};
    submitted = {};
    sampleId = "";
    thresholds = null;
    job = null;
    error = "";
    submitting = false;
    polling = false;
    paused = false;
    sampling = false;
    epoch = 0;
    constructor(changed) {
        this.changed = changed;
    }
    reset() {
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
    async start(port) {
        if (this.submitting || this.polling)
            return;
        const epoch = ++this.epoch;
        this.submitted = { ...this.draft };
        this.job = null;
        this.error = "";
        this.submitting = true;
        this.changed();
        try {
            const job = await port.startRecommendation(this.submitted, this.thresholds ?? undefined);
            if (epoch !== this.epoch)
                return;
            this.job = job;
        }
        catch (err) {
            if (epoch === this.epoch)
                this.error = String(err.message ?? err);
        }
        finally {
            if (epoch === this.epoch) {
                this.submitting = false;
                this.changed();
            }
        }
        if (epoch === this.epoch && this.job)
            await this.poll(port);
    }
    async sample(port) {
        if (this.sampling)
            return null;
        this.sampling = true;
        this.changed();
        try {
            const { record } = await port.matchSample();
            this.sampleId = String(record.record_id ?? "");
            this.draft = { ...this.draft, ...record };
            return { ...record };
        }
        finally {
            this.sampling = false;
            this.changed();
        }
    }
    async poll(port) {
        if (this.polling || !this.job)
            return;
        const epoch = this.epoch;
        this.error = "";
        this.paused = false;
        this.polling = true;
        this.changed();
        try {
            for (let attempt = 0; attempt < 300; attempt++) {
                if (this.job.stage === "complete" || this.job.stage === "failed" || this.job.stage === "cancelled")
                    return;
                const job = await port.recommendation(this.job.id);
                if (epoch !== this.epoch)
                    return;
                this.job = job;
                this.changed();
                if (job.stage === "complete" || job.stage === "failed" || job.stage === "cancelled")
                    return;
                await new Promise((resolve) => setTimeout(resolve, 1000));
                if (epoch !== this.epoch)
                    return;
            }
            this.paused = true;
        }
        catch (err) {
            if (epoch === this.epoch) {
                this.error = String(err.message ?? err);
                this.paused = true;
            }
        }
        finally {
            if (epoch === this.epoch) {
                this.polling = false;
                this.changed();
            }
        }
    }
}
