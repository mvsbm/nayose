// Infra: ApiAdapter (QueryPort over typed FastAPI endpoints) with a promise
// cache (Decorator pattern); LocalStorageStore. The SQL lives SERVER-SIDE in
// typed endpoints; a raw-SQL door exists only behind NAYOSE_DEBUG_SQL.
import {
  ConfusionData,
  EntityDetail,
  EntityHit,
  MetricsViewData,
  QueryPort,
  RecommendResult,
  ReviewPage,
} from "./port.js";

export class ApiError extends Error {}

async function fetchJson(path: string, init?: RequestInit): Promise<unknown> {
  const res = await fetch(path, init);
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new ApiError((detail && detail.detail) || `${path} -> ${res.status}`);
  }
  return res.json();
}

export class ApiAdapter implements QueryPort {
  private runId = "";
  private cache = new Map<string, Promise<unknown>>();

  run(id: string): void {
    this.runId = id;
    this.cache.clear();
  }
  private memo<T>(op: string, args: string, fn: () => Promise<T>): Promise<T> {
    const key = `${this.runId}|${op}|${args}`;
    const hit = this.cache.get(key) as Promise<T> | undefined;
    if (hit) return hit;
    const p = fn().catch((e) => {
      this.cache.delete(key);
      throw e;
    });
    this.cache.set(key, p);
    return p;
  }

  entities(q: string): Promise<EntityHit[]> {
    return this.memo("entities", q, () =>
      fetchJson(`/api/entities?q=${encodeURIComponent(q)}&run_id=${encodeURIComponent(this.runId)}`).then(
        (d) => (d as { rows: EntityHit[] }).rows,
      ),
    );
  }
  entity(id: string): Promise<EntityDetail> {
    return this.memo("entity", id, () =>
      fetchJson(`/api/entity/${encodeURIComponent(id)}?run_id=${encodeURIComponent(this.runId)}`).then(
        (d) => d as EntityDetail,
      ),
    );
  }
  review(minP: number, offset: number, limit: number): Promise<ReviewPage> {
    return this.memo("review", `${minP}:${offset}:${limit}`, () =>
      fetchJson(
        `/api/review?run_id=${encodeURIComponent(this.runId)}&min_p=${minP}&offset=${offset}&limit=${limit}`,
      ).then((d) => d as ReviewPage),
    );
  }
  metrics(): Promise<MetricsViewData> {
    return this.metricsFor(this.runId);
  }
  metricsFor(run: string): Promise<MetricsViewData> {
    return this.memo("metrics", run, () =>
      fetchJson(`/api/metrics?run_id=${encodeURIComponent(run)}`).then((d) => d as MetricsViewData),
    );
  }
  confusion(): Promise<ConfusionData> {
    return this.memo("confusion", "", () =>
      fetchJson(`/api/confusion?run_id=${encodeURIComponent(this.runId)}`).then((d) => d as ConfusionData),
    );
  }
  compare(l: string, r: string): Promise<Record<string, unknown>[]> {
    return this.memo("compare", `${l}:${r}`, () =>
      fetchJson(
        `/api/compare?run_id=${encodeURIComponent(this.runId)}&left=${encodeURIComponent(l)}&right=${encodeURIComponent(r)}`,
      ).then((d) => (d as { rows: Record<string, unknown>[] }).rows),
    );
  }
  recommend(record: Record<string, string>): Promise<RecommendResult> {
    return fetchJson("/api/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: this.runId, record }),
    }).then((d) => d as RecommendResult);
  }
  loadDecisions(): Promise<Array<{ left: string; right: string }>> {
    return fetchJson(`/api/decisions?run_id=${encodeURIComponent(this.runId)}`).then(
      (d) => (d as { items: Array<{ left: string; right: string }> }).items,
    );
  }
  async saveDecisions(items: Array<{ left: string; right: string; ts: string }>): Promise<void> {
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