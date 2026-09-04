// Feature views: each is a pure render function (port, store, container).
// No fetch, no SQL, no storage — all through the QueryPort + Store.
import { QueryPort } from "./port.js";
import type { Store } from "./store.js";
import { asNumber, checkedKey, clamp01, diffFields, likeEscaped } from "./core.js";
import { esc, str } from "./schema.js";

const SKELETON = (k = 3): string =>
  `<div class="skel">${Array.from({ length: k }, () => '<div class="card" style="height:3.2rem"></div>').join("")}</div>`;
const crumb = (...links: string[]): string =>
  `<nav class="crumbs" aria-label="パンくず">${links.map((l, i) => (i % 2 ? `<a href="${esc(links[i - 1]!)}">${esc(l)}</a>` : l)).join(" / ")}</nav>`;
const chip = (label: unknown, kind: "ok" | "warn" | "bad" | "" = ""): string =>
  `<span class="chip-state ${kind}">${esc(label)}</span>`;
const bar = (v: number): string =>
  `<span class="bar"><i style="width:${clamp01(v) * 100}%"></i></span>`;

export async function mountSearch(el: HTMLElement, port: QueryPort, store: Store): Promise<void> {
  el.innerHTML = `<div class="panel">${crumb("#/search", "検索")}
    <div class="card"><h3><label for="q">氏名・カナ・電話・郵便番号で検索</label></h3>
      <input type="search" id="q" placeholder="例：佐藤 太郎" autocomplete="off">
      <p id="stat" role="status" aria-live="polite" class="members"></p>
      <div id="results"></div></div>
    <div class="card"><h3>照合テスト（ライブ /recommend）</h3><div class="toolbar">
      <label>氏名漢字<input id="rt-name" class="bar" type="text"></label>
      <label>カナ<input id="rt-kana" class="bar" type="text"></label>
      <label>電話<input id="rt-phone" class="bar" type="text"></label>
      <button id="rt-go" type="button" class="more">照合</button></div>
      <p id="rt-out" role="status" aria-live="polite" class="members"></p></div></div>`;
  const input = el.querySelector<HTMLInputElement>("#q")!;
  const stat = el.querySelector("#stat")!;
  const results = el.querySelector("#results")!;
  let timer: number | undefined;
  let active = -1;
  const run = async (): Promise<void> => {
    const q = input.value.trim();
    if (q.length < 2) { stat.textContent = ""; results.innerHTML = ""; return; }
    stat.textContent = "検索中…";
    try {
      const hits = await port.entities(q);
      stat.textContent = `${hits.length} 件`;
      results.innerHTML = hits
        .map((r, i) => `<a class="card hit" href="#/entity/${esc(r.nayose_id)}" data-i="${i}" aria-selected="false">
          <h3>${esc(r.name_kanji)} <span class="muted">${esc(r.name_kana || "")}</span></h3>
          <div class="members">${asNumber(r.record_count)} 件 · ${esc(r.phone || "")} · ${esc(r.postal_code || "")}</div></a>`)
        .join("");
      active = -1;
    } catch (e) {
      stat.textContent = "エラー: " + String((e as Error)?.message ?? e);
    }
  };
  input.addEventListener("input", () => { window.clearTimeout(timer); timer = window.setTimeout(() => void run(), 150); });
  input.addEventListener("keydown", (e) => {
    const els = [...results.querySelectorAll<HTMLAnchorElement>(".hit")];
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      active = (active + (e.key === "ArrowDown" ? 1 : els.length - 1)) % Math.max(els.length, 1);
      els.forEach((x, i) => x.setAttribute("aria-selected", String(i === active)));
      if (els[active]) els[active]!.focus();
    } else if (e.key === "Enter" && els[active]) {
      e.preventDefault();
      location.hash = els[active]!.getAttribute("href")!;
    }
  });
  const rtGo = el.querySelector<HTMLButtonElement>("#rt-go")!;
  const rtOut = el.querySelector("#rt-out")!;
  rtGo.addEventListener("click", async () => {
    rtGo.disabled = true;
    rtOut.textContent = "照合中…";
    try {
      const d = await port.recommend({
        name_kanji: el.querySelector<HTMLInputElement>("#rt-name")!.value,
        name_kana: el.querySelector<HTMLInputElement>("#rt-kana")!.value,
        phone: el.querySelector<HTMLInputElement>("#rt-phone")!.value,
      });
      const kind = d.action === "AUTO_LINK" ? "ok" : d.action === "REVIEW" ? "warn" : d.action === "NO_MATCH" ? "bad" : "";
      rtOut.innerHTML = `${chip(d.action, kind)} 確率 ${(d.top_probability * 100).toFixed(1)}% · ${esc(d.top_nayose_id ?? "—")} · 候補 ${d.candidates.length} 件`;
    } catch (err) {
      rtOut.textContent = "エラー: " + String((err as Error)?.message ?? err);
    } finally {
      rtGo.disabled = false;
    }
  });
}

export async function mountReview(el: HTMLElement, port: QueryPort, store: Store): Promise<void> {
  el.innerHTML = `<div class="panel">${SKELETON()}</div>`;
  let offset = 0;
  let minP = 0;
  const key = (l: string, r: string): string => checkedKey(store.run, l, r);
  const load = async (): Promise<void> => {
    const page = await port.review(minP, offset, 200);
    el.innerHTML = `<div class="panel">${crumb("#/review", "レビュー待機")}
      <div class="card"><div class="toolbar">
        <h3 style="margin:0">レビュー待機エッジ</h3>
        <label class="members">最低確率<input type="number" id="minp" step="0.1" min="0" max="1" value="0"></label>
        <span class="members" id="count" role="status" aria-live="polite">${page.total.toLocaleString()} 件 · 既読 ${store.checked.size}</span>
        <button type="button" class="more" id="undo">取り消し</button>
        <button type="button" class="more" id="more">次の 200 件</button></div>
      <table><thead><tr><th>左レコード</th><th>右レコード</th><th>確率</th><th>exact</th><th></th></tr></thead>
      <tbody>${page.rows.map((r) => {
        const done = store.checked.has(key(r.left_record_id, r.right_record_id));
        return `<tr class="${done ? "checked" : ""}" data-l="${esc(r.left_record_id)}" data-r="${esc(r.right_record_id)}">
          <td><a href="#/compare/${esc(r.left_record_id)}/${esc(r.right_record_id)}">${esc(r.left_record_id)}</a></td>
          <td><a href="#/compare/${esc(r.left_record_id)}/${esc(r.right_record_id)}">${esc(r.right_record_id)}</a></td>
          <td>${(asNumber(r.pair_probability) * 100).toFixed(1)}%</td><td>${asNumber(r.exact_count)}</td>
          <td><button type="button" class="check ${done ? "on" : ""}" aria-pressed="${done}">既読</button></td></tr>`;
      }).join("") || '<tr><td colspan="5" class="members">該当なし</td></tr>'}</tbody></table></div></div>`;
    el.querySelector("tbody")!.addEventListener("click", (e) => {
      const btn = (e.target as HTMLElement).closest<HTMLButtonElement>("button.check");
      const tr = (e.target as HTMLElement).closest<HTMLElement>("tr");
      if (!btn || !tr) return;
      store.toggleCheckedKey(key(tr.dataset.l ?? "", tr.dataset.r ?? ""));
      tr.classList.toggle("checked", store.checked.has(key(tr.dataset.l ?? "", tr.dataset.r ?? "")));
      btn.classList.toggle("on");
      btn.setAttribute("aria-pressed", String(store.checked.has(key(tr.dataset.l ?? "", tr.dataset.r ?? ""))));
      el.querySelector("#count")!.textContent = `${page.total.toLocaleString()} 件 · 既読 ${store.checked.size}`;
    });
    el.querySelector("#undo")?.addEventListener("click", () => { store.undo(); void load(); });
    el.querySelector("#minp")?.addEventListener("input", (e) => {
      minP = clamp01(Number((e.target as HTMLInputElement).value) || 0);
      offset = 0;
      void load();
    });
    el.querySelector("#more")?.addEventListener("click", () => { offset += 200; void load(); });
  };
  await load();
}

export async function mountConfusion(el: HTMLElement, port: QueryPort, store: Store): Promise<void> {
  el.innerHTML = `<div class="panel">${SKELETON()}</div>`;
  const data = await port.confusion();
  const actions = ["AUTO_LINK", "REVIEW", "NO_MATCH", "INSUFFICIENT_INPUT"];
  const maxCell = Math.max(1, ...data.scenarios.flatMap((s) => actions.map((a) => s.actions[a] ?? 0)));
  const scenRows = data.scenarios
    .map((s) => `<tr class="scen" tabindex="0" data-scenario="${esc(s.scenario)}"><th scope="row">${esc(s.scenario)}</th>${actions
      .map((a) => { const v = s.actions[a] ?? 0; return `<td class="heat" style="--heat:${Math.round((v / maxCell) * 50)}%">${v}</td>`; })
      .join("")}</tr>`)
    .join("");
  const pairRows = ["true_positives", "false_positives", "true_negatives", "false_negatives", "precision", "recall", "specificity", "f1", "auc"]
    .filter((m) => m in data.pairs)
    .map((m) => `<tr><th scope="row">${esc(m)}</th><td>${Number(data.pairs[m]).toFixed(4)}</td></tr>`)
    .join("");
  el.innerHTML = `<div class="panel">${crumb("#/confusion", "混同行列")}
    <div class="card"><h3>ペア検証器 — 未見テスト分割</h3><table><tbody>${pairRows}</tbody></table></div>
    <div class="card"><h3>クエリ判定（scenario × action）— 行クリックで内訳</h3>
      <table><thead><tr><th>scenario</th>${actions.map((a) => `<th>${esc(a)}</th>`).join("")}</tr></thead>
      <tbody>${scenRows}</tbody></table><div id="drill" role="region" aria-live="polite"></div></div></div>`;
  const drill = el.querySelector("#drill")!;
  el.querySelectorAll("tr.scen").forEach((tr) => {
    const fire = () => {
      const scen = str(tr.getAttribute("data-scenario"));
      const rows = data.decisions[scen] ?? [];
      drill.innerHTML = `<h4 class="muted">${esc(scen)} — ${rows.length} 件</h4>
        <table><thead><tr><th>query</th><th>action</th><th>top entity</th><th>確率</th><th>top1</th></tr></thead>
        <tbody>${rows.map((r) => `<tr><td>${esc(r.query_id)}</td><td>${esc(r.action)}</td><td>${esc(r.top_nayose_id)}</td><td>${(asNumber(r.top_probability) * 100).toFixed(1)}%</td><td>${esc(r.identity_top1_correct)}</td></tr>`).join("")}</tbody></table>`;
    };
    tr.addEventListener("click", fire);
    tr.addEventListener("keydown", (ev: Event) => {
      const e = ev as KeyboardEvent;
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fire(); }
    });
  });
}

export async function mountMetrics(el: HTMLElement, port: QueryPort, store: Store): Promise<void> {
  el.innerHTML = `<div class="panel">${SKELETON()}</div>`;
  const data = await port.metrics();
  el.innerHTML = `<div class="panel">${crumb("#/metrics", "指標")}
    <div class="kpi">${data.kpis.map(([l, v]) => `<div class="card"><div class="v">${(v * 100).toFixed(1)}%</div><div class="l">${esc(l)}</div>${bar(v)}</div>`).join("")}</div>
    <div class="card"><h3>エンティティ</h3><div class="v" style="font-size:1.4rem">${(data.extras.entities ?? 0).toLocaleString()}</div>
      <div class="members">候補エッジ ${(data.extras.edges ?? 0).toLocaleString()} · シングルトン率 ${((data.extras.singleton ?? 0) * 100).toFixed(1)}%</div></div></div>`;
}

export async function mountEntities(el: HTMLElement, port: QueryPort, store: Store): Promise<void> {
  el.innerHTML = `<div class="panel">${SKELETON()}</div>`;
  // reuses search port with empty-ish query -> server caps list
  const hits = await port.entities("");
  el.innerHTML = `<div class="panel">${crumb("#/entities", "エンティティ")}
    <div class="card"><h3>エンティティ一覧（先頭 ${hits.length} 件）</h3>
      ${hits.map((r) => `<div class="card" style="margin:.45rem 0"><h3><a href="#/entity/${esc(r.nayose_id)}">${esc(r.name_kanji)}</a>
        <span class="muted">${esc(r.name_kana || "")}</span></h3><div class="members">${asNumber(r.record_count)} 件</div></div>`).join("") || '<div class="empty">ありません</div>'}
    </div></div>`;
}

export async function mountEntity(el: HTMLElement, port: QueryPort, store: Store, id: string): Promise<void> {
  el.innerHTML = `<div class="panel">${SKELETON()}</div>`;
  const detail = await port.entity(id);
  const e = detail.entity;
  const lin = Object.entries(detail.lineage)
    .map(([f, ts]) => `<div class="lin"><span>${esc(f)}</span>${ts.map((t) => `<span>${esc(t)}</span>`).join("")}</div>`)
    .join("");
  el.innerHTML = `<div class="panel">${crumb("#/entities", "エンティティ", "#/entity/" + esc(id), esc(id))}
    <div class="card"><h2>${esc(e.name_kanji)} <span class="muted">${esc(e.name_kana)}</span></h2>
      <div class="members">${esc(id)} · ${asNumber(e.record_count)} 件 · ${esc(e.date_birth)} · ${esc(e.address)}</div></div>
    ${lin ? `<div class="card"><h3>フィールド由来（lineage）</h3>${lin}</div>` : ""}
    <div class="card"><h3>所属レコード</h3><table><thead><tr><th>record</th><th>カナ</th><th>電話</th></tr></thead>
    <tbody>${detail.members.map((m) => `<tr><td>${esc(m.record_id)}</td><td>${esc(m.name_kana)}</td><td>${esc(m.phone)}</td></tr>`).join("")}</tbody></table></div></div>`;
}

export async function mountCompare(el: HTMLElement, port: QueryPort, store: Store, l: string, r: string): Promise<void> {
  el.innerHTML = `<div class="panel">${SKELETON()}</div>`;
  const rows = await port.compare(l, r);
  const left = rows.find((x) => str(x.record_id) === l);
  const right = rows.find((x) => str(x.record_id) === r);
  if (!left || !right) {
    el.innerHTML = `<div class="panel"><div class="empty">レコードが見つかりません</div></div>`;
    return;
  }
  const fields = diffFields(left);
  const side = (rec: Record<string, unknown>, other: Record<string, unknown>) =>
    `<table><tbody>${fields.map((f) => `<tr><th scope="row">${esc(f)}</th><td class="${str(rec[f]) !== str(other[f]) ? "diff-hi" : ""}">${esc(rec[f])}</td></tr>`).join("")}</tbody></table>`;
  el.innerHTML = `<div class="panel">${crumb("#/review", "レビュー待機")}
    <div class="diff">
      <div class="card"><h3>${esc(l)}</h3>${side(left, right)}</div>
      <div class="card"><h3>${esc(r)}</h3>${side(right, left)}</div>
    </div><p class="members">差分行はハイライト表示。</p></div>`;
}

export async function mountCompareRuns(el: HTMLElement, port: QueryPort, store: Store): Promise<void> {
  const runs = store.runs.map((r) => r.id);
  const a = store.run;
  const b = runs.find((r) => r !== a) ?? a;
  const opts = (sel: string): string =>
    runs.map((r) => `<option value="${esc(r)}" ${r === sel ? "selected" : ""}>${esc(r)}</option>`).join("");
  el.innerHTML = `<div class="panel">${crumb("#/compare-runs", "比較")}
    <div class="card"><div class="toolbar">
      <label class="members">run A<select id="runA">${opts(a)}</select></label>
      <label class="members">run B<select id="runB">${opts(b)}</select></label>
      <button type="button" class="more" id="go">比較</button></div>
      <div id="out" role="region" aria-live="polite"><div class="members">実行バンドル間の主要指標を比較します。</div></div></div></div>`;
  const out = el.querySelector("#out")!;
  const go = async (): Promise<void> => {
    out.innerHTML = SKELETON(2);
    try {
      const [mA, mB] = await Promise.all([
        port.metricsFor(el.querySelector<HTMLSelectElement>("#runA")!.value),
        port.metricsFor(el.querySelector<HTMLSelectElement>("#runB")!.value),
      ]);
      const byA = new Map(mA.kpis), byB = new Map(mB.kpis);
      const rows = [...new Set([...byA.keys(), ...byB.keys()])].map((label) => {
        const va = byA.get(label) ?? 0, vb = byB.get(label) ?? 0;
        const delta = vb - va;
        const cls = Math.abs(delta) > 0.005 ? (delta > 0 ? "ok" : "bad") : "blank";
        return `<tr><th scope="row">${esc(label)}</th><td>${(va * 100).toFixed(1)}%</td><td>${(vb * 100).toFixed(1)}%</td>
          <td class="${cls}">${delta >= 0 ? "+" : ""}${(delta * 100).toFixed(1)}%</td></tr>`;
      }).join("");
      out.innerHTML = `<h4 class="muted">${esc(el.querySelector<HTMLSelectElement>("#runA")!.value)} → ${esc(el.querySelector<HTMLSelectElement>("#runB")!.value)}</h4>
        <table><thead><tr><th>指標</th><th>A</th><th>B</th><th>Δ</th></tr></thead><tbody>${rows}</tbody></table>`;
    } catch (err) {
      out.innerHTML = `<p class="members">エラー: ${esc(String((err as Error)?.message ?? err))}</p>`;
    }
  };
  el.querySelector<HTMLButtonElement>("#go")!.addEventListener("click", () => void go());
}
