export class ApiError extends Error {
}
async function fetchJson(path, init) {
    const res = await fetch(path, init);
    if (!res.ok) {
        const detail = await res.json().catch(() => null);
        throw new ApiError((detail && detail.detail) || `${path} -> ${res.status}`);
    }
    return res.json();
}
export class ApiAdapter {
    runId = "";
    cache = new Map();
    run(id) {
        this.runId = id;
        this.cache.clear();
    }
    memo(op, args, fn) {
        const key = `${this.runId}|${op}|${args}`;
        const hit = this.cache.get(key);
        if (hit)
            return hit;
        const p = fn().catch((e) => {
            this.cache.delete(key);
            throw e;
        });
        this.cache.set(key, p);
        return p;
    }
    entities(q) {
        return this.memo("entities", q, () => fetchJson(`/api/entities?q=${encodeURIComponent(q)}&run_id=${encodeURIComponent(this.runId)}`).then((d) => d.rows));
    }
    entity(id) {
        return this.memo("entity", id, () => fetchJson(`/api/entity/${encodeURIComponent(id)}?run_id=${encodeURIComponent(this.runId)}`).then((d) => d));
    }
    review(minP, offset, limit) {
        return this.memo("review", `${minP}:${offset}:${limit}`, () => fetchJson(`/api/review?run_id=${encodeURIComponent(this.runId)}&min_p=${minP}&offset=${offset}&limit=${limit}`).then((d) => d));
    }
    metrics() {
        return this.metricsFor(this.runId);
    }
    metricsFor(run) {
        return this.memo("metrics", run, () => fetchJson(`/api/metrics?run_id=${encodeURIComponent(run)}`).then((d) => d));
    }
    confusion() {
        return this.memo("confusion", "", () => fetchJson(`/api/confusion?run_id=${encodeURIComponent(this.runId)}`).then((d) => d));
    }
    compare(l, r) {
        return this.memo("compare", `${l}:${r}`, () => fetchJson(`/api/compare?run_id=${encodeURIComponent(this.runId)}&left=${encodeURIComponent(l)}&right=${encodeURIComponent(r)}`).then((d) => d.rows));
    }
    recommend(record) {
        return fetchJson("/api/recommend", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ run_id: this.runId, record }),
        }).then((d) => d);
    }
    loadDecisions() {
        return fetchJson(`/api/decisions?run_id=${encodeURIComponent(this.runId)}`).then((d) => d.items);
    }
    async saveDecisions(items) {
        const res = await fetch("/api/decisions", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ run_id: this.runId, items }),
        });
        if (!res.ok) {
            const detail = await res.json().catch(() => null);
            throw new ApiError((detail && detail.detail) || res.statusText);
        }
    }
}
