// Composition root (hexagonal): build port+store, mount shell, route.
// No side effects at import time; tests call boot({ port, store }) with mocks.
import { ApiAdapter } from "./infra.js";
import { Store } from "./store.js";
import { QueryPort } from "./port.js";
import { checkedKey, parseRoute } from "./core.js";
import { listRuns, RunInfo } from "./shared.js";
import {
  mountCompare,
  mountCompareRuns,
  mountConfusion,
  mountEntities,
  mountEntity,
  mountMetrics,
  mountReview,
  mountSearch,
} from "./features.js";

export interface BootOptions {
  port?: QueryPort;
  store?: Store;
  runs?: RunInfo[];
}

const VIEWS: Record<string, (view: HTMLElement, port: QueryPort, store: Store, args: string[]) => Promise<void>> = {
  search: (v, p, s) => mountSearch(v, p, s),
  review: (v, p, s) => mountReview(v, p, s),
  confusion: (v, p, s) => mountConfusion(v, p, s),
  metrics: (v, p, s) => mountMetrics(v, p, s),
  entities: (v, p, s) => mountEntities(v, p, s),
  entity: (v, p, s, a) => mountEntity(v, p, s, a[0] ?? ""),
  compare: (v, p, s, a) => mountCompare(v, p, s, a[0] ?? "", a[1] ?? ""),
  "compare-runs": (v, p, s) => mountCompareRuns(v, p, s),
};

export async function boot(opts: BootOptions = {}): Promise<{ destroy: () => void }> {
  const store = opts.store ?? new Store();
  const port = opts.port ?? new ApiAdapter();
  const view = document.querySelector<HTMLElement>("main#view")!;
  document.documentElement.dataset.theme = store.theme;

  const paint = async (html: string): Promise<void> => {
    view.innerHTML = `<div class="panel">${html}</div>`;
    view.focus({ preventScroll: true });
  };
  const runView = async (fn: () => Promise<void>): Promise<void> => {
    try {
      await fn();
    } catch (err) {
      paint(`<div class="empty bad"><p>エラー: ${String((err as Error)?.message ?? err)}</p>
        <button type="button" class="more" id="retry">再試行</button></div>`);
      document.querySelector("#retry")?.addEventListener("click", () => void navigate());
    }
  };
  const navigate = async (): Promise<void> => {
    const route = parseRoute(location.hash);
    document.querySelectorAll(".side a").forEach((a) =>
      a.setAttribute("aria-current", a.getAttribute("data-nav") === route.name ? "page" : ""),
    );
    window.scrollTo(0, 0);
    const render = VIEWS[route.name];
    await runView(() => (render ? render(view, port, store, route.args) : mountSearch(view, port, store)));
  };
  window.addEventListener("hashchange", () => void navigate());

  // run selector + first-run guidance
  const sel = document.querySelector<HTMLSelectElement>("#run")!;
  const fp = document.querySelector("#run-fp")!;
  if (opts.runs) {
    store.runs = opts.runs;
  } else {
    try {
      store.runs = await listRuns();
    } catch {
      store.runs = [];
    }
  }
  if (store.runs.length === 0) {
    sel.innerHTML = '<option value="">（なし）</option>';
    await paint(`<div class="empty"><h2>run バンドルが見つかりません</h2>
      <p class="members">生成・学習後に <code>NAYOSE_MODELS_DIR=…</code> を指定して <code>python -m api.main</code> を起動してください。</p></div>`);
    return { destroy: () => window.removeEventListener("hashchange", navigate) };
  }
  const current = store.runs.find((r) => r.current) ?? store.runs[0];
  const saved = store.read("nayose.run");
  store.setRun(store.runs.some((r) => r.id === saved) ? saved ?? "" : current?.id ?? "");
  sel.innerHTML = store.runs
    .map((r) => `<option value="${esc(r.id)}" ${r.id === store.run ? "selected" : ""}>${esc(r.id)}${r.current ? " ★" : ""}</option>`)
    .join("");
  const picked = store.runs.find((r) => r.id === store.run);
  fp.textContent = picked?.fingerprint ? `fingerprint ${esc(picked.fingerprint)}` : "";
  port.run(store.run);
  sel.addEventListener("change", () => {
    store.setRun(sel.value);
    store.write("nayose.run", store.run);
    port.run(store.run);
    store.clearRunState();
    const p = store.runs.find((r) => r.id === store.run);
    fp.textContent = p?.fingerprint ? `fingerprint ${esc(p.fingerprint)}` : "";
    void navigate();
  });

  // decision persistence: localStorage (offline) + server-side (survives machines)
  store.checked = new Set<string>(JSON.parse(store.read("nayose.checked") ?? "[]"));
  store.on("checked", () => store.write("nayose.checked", JSON.stringify([...store.checked])));
  try {
    const remote = await port.loadDecisions();
    if (remote.length) {
      store.checked = new Set(remote.map((d) => checkedKey(store.run, d.left, d.right)));
    }
  } catch {
    /* server unreachable -> keep localStorage state */
  }
  let saveTimer: number | undefined;
  store.on("checked", () => {
    store.write("nayose.checked", JSON.stringify([...store.checked]));
    window.clearTimeout(saveTimer);
    saveTimer = window.setTimeout(() => {
      const now = new Date().toISOString();
      const items = [...store.checked].map((key) => {
        const parts = key.split("|");
        return { left: parts[1] ?? "", right: parts[2] ?? "", ts: now };
      });
      void port.saveDecisions(items).catch(() => {});
    }, 500);
  });

  // theme
  document.querySelector("#theme")?.addEventListener("click", () => {
    store.toggleTheme();
    store.write("nayose.theme", store.theme);
  });

  // ⌘K palette (keyboard-first) — powered by the same QueryPort
  const palette = document.querySelector<HTMLElement>("#palette")!;
  const pInput = document.querySelector<HTMLInputElement>("#palette-input")!;
  const pList = document.querySelector<HTMLUListElement>("#palette-results")!;
  const pStat = document.querySelector("#palette-stat")!;
  let pItems: Array<{ label: string; meta: string; go: () => void }> = [];
  let pActive = -1;
  const openPalette = (): void => { palette.classList.add("open"); palette.hidden = false; pInput.value = ""; pStat.textContent = ""; void paletteSearch(""); pInput.focus(); };
  const closePalette = (): void => { palette.classList.remove("open"); palette.hidden = true; pActive = -1; };
  async function paletteSearch(q: string): Promise<void> {
    const nav: Array<[string, string]> = [["search", "検索"], ["review", "レビュー待機"], ["entities", "エンティティ"], ["confusion", "混同行列"], ["metrics", "指標"], ["compare-runs", "比較"]];
    type Item = { label: string; meta: string; go: () => void };
    const items: Item[] = nav.map(([id, label]) => ({ label, meta: `#/${id}`, go: () => { closePalette(); location.hash = `#/${id}`; } }));
    if (q.length >= 2) {
      pStat.textContent = "検索中…";
      try {
        const hits = await port.entities(q);
        hits.forEach((h) => items.push({ label: `${h.name_kanji} ${h.name_kana ?? ""}`, meta: h.nayose_id, go: () => { closePalette(); location.hash = `#/entity/${h.nayose_id}`; } }));
        pStat.textContent = `${hits.length} 件`;
      } catch (err) {
        pStat.textContent = "エラー: " + String((err as Error)?.message ?? err);
      }
    }
    pItems = items.slice(0, 14);
    pActive = pItems.length ? 0 : -1;
    renderPaletteList();
  }
  const renderPaletteList = (): void => {
    pList.innerHTML = pItems
      .map((it, i) => `<li role="option" id="p-${i}" aria-selected="${i === pActive}" data-i="${i}"><span>${esc(it.label)}</span><span class="muted">${esc(it.meta)}</span></li>`)
      .join("");
    pList.setAttribute("aria-activedescendant", pActive >= 0 ? `p-${pActive}` : "");
    pList.querySelector(`[data-i="${pActive}"]`)?.scrollIntoView({ block: "nearest" });
  };
  pInput.addEventListener("input", () => void paletteSearch(pInput.value));
  pInput.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (pItems.length) { pActive = (pActive + (e.key === "ArrowDown" ? 1 : pItems.length - 1)) % pItems.length; renderPaletteList(); }
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (pActive >= 0 && pItems[pActive]) pItems[pActive]!.go();
    } else if (e.key === "Escape") { closePalette(); }
  });
  pList.addEventListener("click", (e) => {
    const li = (e.target as HTMLElement).closest<HTMLElement>("li");
    const i = Number(li?.dataset.i);
    if (pItems[i]) pItems[i]!.go();
  });
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); }
    else if (e.key === "Escape") closePalette();
  });
  palette.addEventListener("click", (e) => { if (e.target === palette) closePalette(); });

  await navigate();
  return { destroy: () => { window.removeEventListener("hashchange", navigate); document.removeEventListener("keydown", () => {}); } };
}

const esc = (s: unknown): string =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));

// Real browser: auto-start. Tests import this module with a ?boot=N query
// (to control instantiation) and call boot() themselves — no side effects on import.
if (new URL(import.meta.url).searchParams.get("boot") === null) {
  void boot();
}
