// Nayose 照合監査 — typed, no-build app (strict TS -> ESM JS).
// P0/P1: search-first default; keyboard nav (palette + results); terse live
// status; review workflow (count, filter, checked-persisted); focus on route;
// skeletons; first-run guidance; error+retry; compare breadcrumb+entity links;
// confusion heat + scenario drill; metrics bars; lineage query cache.
import { query, listRuns, recommend, clearQueryCache, ApiError } from "./api.js";
import { expectColumns, num, str } from "./schema.js";

/* ---- state ---- */
const state = {
  run: localStorage.getItem("nayose.run") ?? "",
  theme: (localStorage.getItem("nayose.theme") as "light" | "dark") || "light",
  runs: [] as Array<{ id: string; fingerprint?: string; current: boolean }>,
  checked: new Set<string>(),
};
document.documentElement.dataset.theme = state.theme;

const view = document.querySelector<HTMLElement>("main#view")!;
const esc = (s: unknown): string => str(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));
const like = (q: string): string => q.replace(/[%_\\]/g, (c) => "\\" + c);
const checkedKey = (l: string, r: string): string => `${state.run}|${l}|${r}`;

/* ---- paint plumbing: skeleton -> render -> focus (P0) ---- */
function paint(html: string): void {
  view.innerHTML = `<div class="panel">${html}</div>`;
  view.focus({ preventScroll: true });
}
const skeleton = (k = 4): string =>
  `<div class="skel">${Array.from({ length: k }, () => '<div class="card" style="height:3.2rem"></div>').join("")}</div>`;
function crumb(...links: string[]): string {
  return `<nav class="crumbs" aria-label="パンくず">${links.map((l, i) => (i % 2 ? `<a href="${esc(links[i - 1]!)}">${esc(l)}</a>` : "")).join(" / ")}</nav>`;
}
const chip = (label: unknown, kind: "ok" | "warn" | "bad" | "" = ""): string =>
  `<span class="chip-state ${kind}">${esc(label)}</span>`;
const bar = (v: number): string =>
  `<span class="bar"><i style="width:${Math.min(100, Math.max(0, v * 100)).toFixed(1)}%"></i></span>`;

async function runView(fn: () => Promise<void>): Promise<void> {
  paint(skeleton(3));
  try {
    await fn();
  } catch (err) {
    const msg = err instanceof ApiError ? err.message : String((err as Error)?.message ?? err);
    paint(`<div class="empty bad"><p>エラー: ${esc(msg)}</p>
      <button type="button" class="more" id="retry">再試行</button></div>`);
    document.querySelector("#retry")?.addEventListener("click", () => void navigate());
  }
}

/* ---- views (task-shaped: search -> review -> entities -> audit) ---- */
async function viewSearch(): Promise<void> {
  paint(`${crumb("#/search", "検索")}
    <div class="card"><h3><label for="q">氏名・カナ・電話・郵便番号で検索</label></h3>
      <input type="search" id="q" name="q" placeholder="例：佐藤 太郎" autocomplete="off">
      <p id="stat" role="status" aria-live="polite" class="members"></p>
      <div id="results"></div></div>
    <div class="card"><h3>照合テスト（ライブ /recommend）</h3>
      <div class="toolbar">
        <label>氏名漢字<input id="rt-name" class="bar" type="text" placeholder="佐藤 太郎"></label>
        <label>カナ<input id="rt-kana" class="bar" type="text" placeholder="サトウ タロウ"></label>
        <label>電話<input id="rt-phone" class="bar" type="text" placeholder="090-1234-5678"></label>
        <button id="rt-go" type="button" class="more">照合</button>
      </div>
      <p id="rt-out" role="status" aria-live="polite" class="members"></p>
    </div>`);

  const input = document.querySelector<HTMLInputElement>("#q")!;
  const stat = document.querySelector("#stat")!;
  const results = document.querySelector("#results")!;
  let timer: number | undefined;
  let active = -1;
  const run = async (): Promise<void> => {
    const q = input.value.trim();
    if (q.length < 2) {
      stat.textContent = "";
      results.innerHTML = "";
      return;
    }
    stat.textContent = "検索中…";
    try {
      const rows = await query(
        `SELECT nayose_id,name_kanji,name_kana,phone,postal_code,record_count
         FROM 'bundle/canonical_entities.parquet'
         WHERE name_kanji LIKE '%${like(q)}%' OR name_kana LIKE '%${like(q)}%'
           OR phone LIKE '%${like(q)}%' OR postal_code LIKE '%${like(q)}%' LIMIT 50`,
        state.run,
      );
      expectColumns(rows, "nayose_id", "name_kanji", "record_count");
      stat.textContent = `${rows.length} 件`;
      results.innerHTML = rows
        .map(
          (r, i) =>
            `<a class="card hit" href="#/entity/${esc(r.nayose_id)}" data-idx="${i}" aria-selected="false">
             <h3>${esc(r.name_kanji)} <span class="muted">${esc(r.name_kana)}</span></h3>
             <div class="members">${num(r.record_count)} 件 · ${esc(r.phone)} · ${esc(r.postal_code)}</div></a>`,
        )
        .join("");
      active = -1;
    } catch (err) {
      stat.textContent = "エラー: " + String((err as Error)?.message ?? err);
    }
  };
  input.addEventListener("input", () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => void run(), 150);
  });
  input.addEventListener("keydown", (e) => {
    const els = Array.from(results.querySelectorAll<HTMLAnchorElement>(".hit"));
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      active = (active + (e.key === "ArrowDown" ? 1 : els.length - 1)) % Math.max(els.length, 1);
      els.forEach((el, i) => el.setAttribute("aria-selected", String(i === active)));
      if (els[active]) els[active]!.focus();
    } else if (e.key === "Enter" && active >= 0 && els[active]) {
      e.preventDefault();
      location.hash = els[active]!.getAttribute("href")!;
    }
  });
  results.addEventListener("click", (e) => {
    const a = (e.target as HTMLElement).closest<HTMLAnchorElement>(".hit");
    if (a) location.hash = a.getAttribute("href")!;
  });

  const rtGo = document.querySelector<HTMLButtonElement>("#rt-go")!;
  const rtOut = document.querySelector("#rt-out")!;
  rtGo.addEventListener("click", async () => {
    rtGo.disabled = true;
    rtOut.textContent = "照合中…";
    try {
      const d = await recommend(
        {
          name_kanji: document.querySelector<HTMLInputElement>("#rt-name")!.value,
          name_kana: document.querySelector<HTMLInputElement>("#rt-kana")!.value,
          phone: document.querySelector<HTMLInputElement>("#rt-phone")!.value,
        },
        state.run,
      );
      const kind = d.action === "AUTO_LINK" ? "ok" : d.action === "REVIEW" ? "warn" : d.action === "NO_MATCH" ? "bad" : "";
      rtOut.innerHTML = `${chip(d.action, kind)} 確率 ${(d.top_probability * 100).toFixed(1)}% · ${esc(d.top_nayose_id ?? "—")} · 候補 ${d.candidates.length} 件`;
    } catch (err) {
      rtOut.textContent = "エラー: " + String((err as Error)?.message ?? err);
    } finally {
      rtGo.disabled = false;
    }
  });
}

async function viewReview(): Promise<void> {
  const [totalRows] = await query(`SELECT count(*) AS c FROM 'bundle/review_queue.parquet'`, state.run);
  const total = num(totalRows?.c);
  let offset = 0;
  let minP = 0;

  const load = async (): Promise<void> => {
    const rows = await query(
      `SELECT left_record_id,right_record_id,pair_probability,exact_count
        FROM 'bundle/review_queue.parquet'
        WHERE pair_probability >= ${minP}
        ORDER BY pair_probability DESC LIMIT 200 OFFSET ${offset}`,
      state.run,
    );
    expectColumns(rows, "left_record_id", "right_record_id", "pair_probability");
    paint(`${crumb("#/review", "レビュー待機")}
      <div class="card"><div class="toolbar">
        <h3 style="margin:0">レビュー待機エッジ</h3>
        <label class="members">最低確率
          <input type="number" id="minp" step="0.1" min="0" max="1" value="0" style="width:6rem">
        </label>
        <span class="members" id="count" role="status" aria-live="polite">${total.toLocaleString()} 件 · 既読 ${state.checked.size}</span>
        <button type="button" class="more" id="more">次の 200 件</button>
      </div>
      <table><thead><tr><th>左レコード</th><th>右レコード</th><th>確率</th><th>exact</th><th></th></tr></thead>
      <tbody>${rows.map((r) => {
        const l = str(r.left_record_id);
        const rk = str(r.right_record_id);
        const done = state.checked.has(checkedKey(l, rk));
        return `<tr class="${done ? "checked" : ""}" data-l="${esc(l)}" data-r="${esc(rk)}">
          <td><a href="#/compare/${esc(l)}/${esc(rk)}">${esc(l)}</a></td>
          <td><a href="#/compare/${esc(l)}/${esc(rk)}">${esc(rk)}</a></td>
          <td>${(num(r.pair_probability) * 100).toFixed(1)}%</td><td>${num(r.exact_count)}</td>
          <td><button type="button" class="check ${done ? "on" : ""}" aria-pressed="${done}">既読</button></td></tr>`;
      }).join("") || '<tr><td colspan="5" class="members">該当なし</td></tr>'}</tbody></table></div>`);
    const tbody = view.querySelector("tbody")!;
    tbody.addEventListener("click", (e) => {
      const btn = (e.target as HTMLElement).closest<HTMLButtonElement>("button.check");
      const tr = (e.target as HTMLElement).closest<HTMLElement>("tr");
      if (!btn || !tr) return;
      const key = checkedKey(tr.dataset.l ?? "", tr.dataset.r ?? "");
      if (state.checked.has(key)) state.checked.delete(key);
      else state.checked.add(key);
      localStorage.setItem("nayose.checked", JSON.stringify([...state.checked]));
      tr.classList.toggle("checked");
      btn.classList.toggle("on");
      btn.setAttribute("aria-pressed", String(state.checked.has(key)));
    });
    view.querySelector("#minp")?.addEventListener("input", (e) => {
      minP = Math.min(1, Math.max(0, Number((e.target as HTMLInputElement).value) || 0));
      offset = 0;
      void load();
    });
    view.querySelector("#more")?.addEventListener("click", () => {
      offset += 200;
      void load();
    });
  };
  await load();
}

async function viewConfusion(): Promise<void> {
  const [pc, qc] = await Promise.all([
    query("SELECT metric,value FROM 'bundle/pair_confusion_test.parquet'", state.run),
    query("SELECT scenario,action,expected_yes FROM 'bundle/query_confusion.parquet'", state.run),
  ]);
  const pm = new Map(pc.map((r) => [str(r.metric), num(r.value)]));
  const pairRows = ["true_positives", "false_positives", "true_negatives", "false_negatives", "precision", "recall", "specificity", "f1", "auc"]
    .filter((m) => pm.has(m))
    .map((m) => `<tr><th scope="row">${esc(m)}</th><td>${(pm.get(m) ?? 0).toFixed(4)}</td></tr>`)
    .join("");
  const actions = ["AUTO_LINK", "REVIEW", "NO_MATCH", "INSUFFICIENT_INPUT"];
  const grid = new Map<string, Map<string, number>>();
  qc.forEach((r) => {
    const row = grid.get(str(r.scenario)) ?? new Map<string, number>();
    row.set(str(r.action), num(r.expected_yes));
    grid.set(str(r.scenario), row);
  });
  const maxCell = Math.max(1, ...[...grid.values()].flatMap((m) => [...m.values()]));
  const scenRows = [...grid.entries()]
    .map(([s, m]) => {
      const cells = actions
        .map((a) => `<td class="heat" style="--heat:${Math.round((((m.get(a) ?? 0) / maxCell) * 100) / 2)}%">${m.get(a) ?? 0}</td>`)
        .join("");
      return `<tr class="scen" tabindex="0" data-scenario="${esc(s)}"><th scope="row">${esc(s)}</th>${cells}</tr>`;
    })
    .join("");
  paint(`${crumb("#/confusion", "混同行列")}
    <div class="card"><h3>ペア検証器 — 未見テスト分割</h3><table><tbody>${pairRows}</tbody></table></div>
    <div class="card"><h3>クエリ判定（scenario × action）— 行クリックで内訳</h3>
      <table><thead><tr><th>scenario</th>${actions.map((a) => `<th>${esc(a)}</th>`).join("")}</tr></thead>
      <tbody>${scenRows}</tbody></table>
      <div id="drill" role="region" aria-live="polite"></div></div>`);

  const drill = view.querySelector("#drill")!;
  const openDrill = async (scenario: string): Promise<void> => {
    drill.innerHTML = skeleton(2);
    try {
      const rows = await query(
        `SELECT query_id,action,top_nayose_id,top_probability,identity_top1_correct
          FROM 'bundle/query_results.parquet' WHERE scenario = '${esc(scenario)}' LIMIT 50`,
        state.run,
      );
      drill.innerHTML = `<h4 class="muted">${esc(scenario)} — 決定の内訳</h4>
        <table><thead><tr><th>query</th><th>action</th><th>top entity</th><th>確率</th><th>top1</th></tr></thead>
        <tbody>${rows.map((r) =>
          `<tr><td>${esc(r.query_id)}</td><td>${esc(r.action)}</td><td>${esc(r.top_nayose_id)}</td>
           <td>${(num(r.top_probability) * 100).toFixed(1)}%</td><td>${esc(r.identity_top1_correct)}</td></tr>`).join("")}</tbody></table>`;
    } catch (err) {
      drill.innerHTML = `<p class="members">エラー: ${esc(String((err as Error)?.message ?? err))}</p>`;
    }
  };
  view.querySelectorAll("tr.scen").forEach((tr) => {
    const fire = () => void openDrill(str(tr.getAttribute("data-scenario")));
    tr.addEventListener("click", fire);
    tr.addEventListener("keydown", (ev: Event) => { const e = ev as KeyboardEvent;
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        fire();
      }
    });
  });
}

async function viewMetrics(): Promise<void> {
  const [sum, met] = await Promise.all([
    query("SELECT metric,value FROM 'bundle/full_dataset_summary.parquet'", state.run),
    query("SELECT section,metric,value FROM 'bundle/full_dataset_metrics.parquet'", state.run),
  ]);
  const m = new Map(sum.map((r) => [str(r.metric), num(r.value)]));
  const qm = new Map(
    met.filter((r) => str(r.section) === "incoming_query").map((r) => [str(r.metric), num(r.value)]),
  );
  const p = m.get("b3_precision") ?? 0;
  const r = m.get("b3_recall") ?? 0;
  const b3 = p + r > 0 ? (2 * p * r) / (p + r) : 0;
  const kpis: Array<[string, number]> = [
    ["candidate recall", m.get("candidate_pair_recall") ?? 0],
    ["B³ F1", b3],
    ["top-1 identity", qm.get("top1_identity_accuracy_existing") ?? 0],
    ["MRR", qm.get("mean_reciprocal_rank_existing") ?? 0],
    ["review", qm.get("review_rate") ?? 0],
    ["no-match", qm.get("no_match_rate") ?? 0],
  ];
  paint(`${crumb("#/metrics", "指標")}
    <div class="kpi">${kpis.map(([l, v]) => `<div class="card"><div class="v">${(v * 100).toFixed(1)}%</div><div class="l">${esc(l)}</div>${bar(v)}</div>`).join("")}</div>
    <div class="card"><h3>エンティティ</h3>
      <div class="v" style="font-size:1.4rem">${(m.get("predicted_entities") ?? 0).toLocaleString()}</div>
      <div class="members">候補エッジ ${(m.get("candidate_edges") ?? 0).toLocaleString()} · シングルトン率 ${((m.get("singleton_entity_rate") ?? 0) * 100).toFixed(1)}%</div>
    </div>`);
}

async function viewEntities(): Promise<void> {
  const rows = await query(
    `SELECT nayose_id,name_kanji,name_kana,record_count FROM 'bundle/canonical_entities.parquet'
     ORDER BY record_count DESC LIMIT 100`,
    state.run,
  );
  paint(`${crumb("#/entities", "エンティティ")}
    <div class="card"><h3>エンティティ一覧（多レコード順・先頭100件）</h3>
    ${rows.map((r) =>
      `<div class="card" style="margin:.45rem 0"><h3><a href="#/entity/${esc(r.nayose_id)}">${esc(r.name_kanji)}</a>
       <span class="muted">${esc(r.name_kana)}</span></h3><div class="members">${num(r.record_count)} 件</div></div>`).join("") || '<div class="empty">ありません</div>'}
    </div>`);
}

const lineageCache = new Map<string, Promise<Array<{ field: string; transformation: string; c: number }>>>();
async function viewEntity(id: string): Promise<void> {
  const [ent, members] = await Promise.all([
    query(`SELECT * FROM 'bundle/canonical_entities.parquet' WHERE nayose_id = '${esc(id)}' LIMIT 1`, state.run),
    query(
      `SELECT r.record_id,r.name_kana,r.postal_code,r.phone,r.email FROM 'data/reference_records.parquet' r
       JOIN 'bundle/record_to_nayose.parquet' m ON r.record_id = m.record_id
       WHERE m.nayose_id = '${esc(id)}' LIMIT 8`,
      state.run,
    ),
  ]);
  if (!ent[0]) {
    paint(`<div class="empty">エンティティ ${esc(id)} が見つかりません</div>`);
    return;
  }
  const e = ent[0];
  let lin = "";
  if (members.length > 0) {
    const ids = members.map((m) => `'${esc(m.record_id)}'`).join(",");
    const key = `${state.run}|${ids.length}`;
    const fetchLineage = async () => {
      const rows = await query(
        `SELECT field,transformation,count(*) AS c FROM 'data/ground_truth/field_lineage.parquet'
         WHERE item_id IN (${ids}) GROUP BY field,transformation ORDER BY field,c DESC`,
        state.run,
      );
      return rows.map((r) => ({ field: str(r.field), transformation: str(r.transformation), c: num(r.c) }));
    };
    const rows =
      lineageCache.get(key) ??
      (lineageCache.set(key, fetchLineage().catch((err) => { lineageCache.delete(key); throw err; })), lineageCache.get(key)!);
    const ls = await rows;
    const by = new Map<string, string[]>();
    ls.forEach((r) => {
      const arr = by.get(r.field) ?? [];
      arr.push(r.transformation);
      by.set(r.field, arr);
    });
    lin = [...by.entries()]
      .map(([f, ts]) => `<div class="lin"><span>${esc(f)}</span>${ts.map((t) => `<span>${esc(t)}</span>`).join("")}</div>`)
      .join("");
  }
  paint(`${crumb("#/entities", "エンティティ", "#/entity/" + esc(id), esc(id))}
    <div class="card"><h2>${esc(e.name_kanji)} <span class="muted">${esc(e.name_kana)}</span></h2>
      <div class="members">${esc(id)} · ${num(e.record_count)} 件 · ${esc(e.date_birth)} · ${esc(e.address)}</div></div>
    ${lin ? `<div class="card"><h3>フィールド由来（lineage）</h3>${lin}</div>` : ""}
    <div class="card"><h3>所属レコード</h3><table><thead><tr><th>record</th><th>カナ</th><th>電話</th></tr></thead>
    <tbody>${members.map((m) => `<tr><td>${esc(m.record_id)}</td><td>${esc(m.name_kana)}</td><td>${esc(m.phone)}</td></tr>`).join("")}</tbody></table></div>`);
}

async function viewCompare(l: string, r: string): Promise<void> {
  const [rows, mapRows] = await Promise.all([
    query(`SELECT * FROM 'data/reference_records.parquet' WHERE record_id IN ('${esc(l)}','${esc(r)}')`, state.run),
    query(`SELECT record_id,nayose_id FROM 'bundle/record_to_nayose.parquet' WHERE record_id IN ('${esc(l)}','${esc(r)}')`, state.run),
  ]);
  const byId = new Map(rows.map((x) => [str(x.record_id), x]));
  const left = byId.get(l);
  const right = byId.get(r);
  if (!left || !right) {
    paint(`<div class="empty">レコードが見つかりません</div>`);
    return;
  }
  const entityOf = new Map(mapRows.map((x) => [str(x.record_id), str(x.nayose_id)]));
  const fields = Object.keys(left).filter((f) => !["record_id", "policy_id", "observed_at"].includes(f));
  const side = (rec: Record<string, unknown>, other: Record<string, unknown>) =>
    `<table><tbody>${fields
      .map((f) => `<tr><th scope="row">${esc(f)}</th><td class="${str(rec[f]) !== str(other[f]) ? "diff-hi" : ""}">${esc(rec[f])}</td></tr>`)
      .join("")}</tbody></table>`;
  paint(`${crumb("#/review", "レビュー待機")}
    <div class="diff">
      <div class="card"><h3>${esc(l)}</h3>${entityOf.get(l) ? `<div class="members"><a href="#/entity/${esc(entityOf.get(l)!)}">エンティティ ${esc(entityOf.get(l)!)} を開く</a></div>` : ""}${side(left, right)}</div>
      <div class="card"><h3>${esc(r)}</h3>${entityOf.get(r) ? `<div class="members"><a href="#/entity/${esc(entityOf.get(r)!)}">エンティティ ${esc(entityOf.get(r)!)} を開く</a></div>` : ""}${side(right, left)}</div>
    </div>
    <p class="members">差分行はハイライト表示。</p>`);
}

/* ---- router (URL as state; default = search, task-first) ---- */
type Route = { name: string; args: string[] };
function parseRoute(): Route {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter((p) => p.length > 0);
  return { name: parts[0] ?? "search", args: parts.slice(1) };
}
const VIEWS = new Map<string, (args: string[]) => Promise<void>>([
  ["search", () => viewSearch()],
  ["review", () => viewReview()],
  ["confusion", () => viewConfusion()],
  ["metrics", () => viewMetrics()],
  ["entities", () => viewEntities()],
  ["entity", (a) => viewEntity(decodeURIComponent(a[0] ?? ""))],
  ["compare", (a) => viewCompare(decodeURIComponent(a[0] ?? ""), decodeURIComponent(a[1] ?? ""))],
]);
async function navigate(): Promise<void> {
  const route = parseRoute();
  document.querySelectorAll(".side a").forEach((a) =>
    a.setAttribute("aria-current", a.getAttribute("data-nav") === route.name ? "page" : ""),
  );
  window.scrollTo(0, 0);
  const fn = VIEWS.get(route.name);
  await runView(() => (fn ? fn(route.args) : viewSearch()));
}
window.addEventListener("hashchange", () => void navigate());

/* ---- run selector + first-run guidance + theme ---- */
async function initRuns(): Promise<void> {
  state.runs = await listRuns();
  const sel = document.querySelector<HTMLSelectElement>("#run")!;
  const fp = document.querySelector("#run-fp")!;
  if (state.runs.length === 0) {
    sel.innerHTML = '<option value="">（なし）</option>';
    paint(`<div class="empty"><h2>run バンドルが見つかりません</h2>
      <p class="members">生成・学習後に <code>NAYOSE_MODELS_DIR=…</code> を指定してサービスを起動してください（例: <code>python -m api.main</code> の環境変数）。</p></div>`);
    return;
  }
  const current = state.runs.find((r) => r.current) ?? state.runs[0];
  if (!state.runs.some((r) => r.id === state.run)) {
    state.run = current?.id ?? "";
  }
  sel.innerHTML = state.runs
    .map((r) => `<option value="${esc(r.id)}" ${r.id === state.run ? "selected" : ""}>${esc(r.id)}${r.current ? " ★" : ""}</option>`)
    .join("");
  const pick = state.runs.find((r) => r.id === state.run);
  fp.textContent = pick?.fingerprint ? `fingerprint ${esc(pick.fingerprint)}` : "";
  sel.addEventListener("change", () => {
    state.run = sel.value;
    localStorage.setItem("nayose.run", state.run);
    const picked = state.runs.find((r) => r.id === state.run);
    fp.textContent = picked?.fingerprint ? `fingerprint ${esc(picked.fingerprint)}` : "";
    clearQueryCache();
    lineageCache.clear();
    state.checked = new Set<string>(JSON.parse(localStorage.getItem("nayose.checked") ?? "[]"));
    void navigate();
  });
}

document.querySelector("#theme")?.addEventListener("click", () => {
  state.theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = state.theme;
  localStorage.setItem("nayose.theme", state.theme);
});

/* ---- ⌘K palette (keyboard-first) ---- */
const palette = document.querySelector<HTMLElement>("#palette")!;
const pInput = document.querySelector<HTMLInputElement>("#palette-input")!;
const pList = document.querySelector<HTMLUListElement>("#palette-results")!;
const pStat = document.querySelector("#palette-stat")!;
let pItems: Array<{ label: string; meta: string; go: () => void }> = [];
let pActive = -1;

function openPalette(): void {
  palette.hidden = false;
  palette.classList.add("open");
  pInput.value = "";
  pStat.textContent = "";
  void paletteSearch("");
  pInput.focus();
}
function closePalette(): void {
  palette.classList.remove("open");
  palette.hidden = true;
  pActive = -1;
}
function renderPalette(): void {
  pList.innerHTML = pItems
    .map(
      (it, i) =>
        `<li role="option" id="p-${i}" aria-selected="${i === pActive}" data-i="${i}">
         <span>${esc(it.label)}</span><span class="muted">${esc(it.meta)}</span></li>`,
    )
    .join("");
  pList.setAttribute("aria-activedescendant", pActive >= 0 ? `p-${pActive}` : "");
  pList.querySelector(`[data-i="${pActive}"]`)?.scrollIntoView({ block: "nearest" });
}
async function paletteSearch(q: string): Promise<void> {
  const nav: Array<[string, string]> = [
    ["search", "検索"], ["review", "レビュー待機"], ["entities", "エンティティ"],
    ["confusion", "混同行列"], ["metrics", "指標"],
  ];
  pItems = nav.map(([id, label]) => ({ label, meta: `#/${id}`, go: () => { closePalette(); location.hash = `#/${id}`; } }));
  if (q.length >= 2) {
    pStat.textContent = "検索中…";
    try {
      const rows = await query(
        `SELECT nayose_id,name_kanji,name_kana FROM 'bundle/canonical_entities.parquet'
         WHERE name_kanji LIKE '%${like(q)}%' OR name_kana LIKE '%${like(q)}%' OR nayose_id LIKE '%${like(q)}%' LIMIT 12`,
        state.run,
      );
      rows.forEach((r) =>
        pItems.push({
          label: `${str(r.name_kanji)} ${str(r.name_kana)}`,
          meta: str(r.nayose_id),
          go: () => { closePalette(); location.hash = `#/entity/${str(r.nayose_id)}`; },
        }),
      );
      pStat.textContent = `${rows.length} 件`;
    } catch (err) {
      pStat.textContent = "エラー: " + String((err as Error)?.message ?? err);
    }
  }
  pActive = pItems.length ? 0 : -1;
  renderPalette();
}
pInput.addEventListener("input", () => void paletteSearch(pInput.value));
pInput.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    if (pItems.length) {
      pActive = (pActive + (e.key === "ArrowDown" ? 1 : pItems.length - 1)) % pItems.length;
      renderPalette();
    }
  } else if (e.key === "Enter") {
    e.preventDefault();
    if (pActive >= 0 && pItems[pActive]) pItems[pActive]!.go();
  } else if (e.key === "Escape") {
    closePalette();
  }
});
pList.addEventListener("click", (e) => {
  const li = (e.target as HTMLElement).closest<HTMLElement>("li");
  if (li) {
    const i = Number(li.dataset.i);
    if (pItems[i]) pItems[i]!.go();
  }
});
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    openPalette();
  } else if (e.key === "Escape") {
    closePalette();
  }
});
palette.addEventListener("click", (e) => {
  if (e.target === palette) closePalette();
});

/* ---- boot: runs -> checked -> navigate ---- */
state.checked = new Set<string>(JSON.parse(localStorage.getItem("nayose.checked") ?? "[]"));
initRuns()
  .then(() => navigate())
  .catch((err) => paint(`<div class="empty bad">初期化エラー: ${esc(String((err as Error)?.message ?? err))}</div>`));