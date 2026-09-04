/* Nayose 照合監査 — no-build ESM app.
 * Design principles (consensus, 2025): calm tech; default-view-is-the-answer;
 * epistemic honesty (uncertainty + provenance first); direct manipulation (<100ms);
 * URL as state (routes); task-shaped IA; local-first (DuckDB over parquet, one
 * local server); motion as meaning; reduced-motion respected; a11y as aesthetics.
 * Data access is a thin adapter so the DuckDB engine can move client-side (WASM)
 * by swapping `query()` for one function.
 */
"use strict";

/* ---- state ---- */
const state = { run: localStorage.getItem("nayose.run") || "", theme: localStorage.getItem("nayose.theme") || "light" };
document.documentElement.dataset.theme = state.theme;

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const like = (q) => q.replace(/[%_\\]/g, (c) => "\\" + c);

/* ---- data adapter (DuckDB via server; swap for WASM later) ---- */
async function query(sql) {
  const res = await fetch("/api/query", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ run_id: state.run, sql }),
  });
  if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
  const data = await res.json();
  const rows = data.rows.map((r) => Object.fromEntries(data.columns.map((c, i) => [c, r[i]])));
  return rows;
}

/* ---- tiny render helpers ---- */
const view = $("#view");
let count = 0;
function paint(html) { view.innerHTML = `<div class="panel">${html}</div>`; }
function busy(msg = "読み込み中…") { paint(`<div class="empty">${esc(msg)}</div>`); }
function chip(label, kind = "") { return `<span class="chip-state ${kind}">${esc(label)}</span>`; }
async function run(fn) {
  busy();
  try { await fn(); } catch (e) { paint(`<div class="empty bad">エラー: ${esc(e.message)}</div>`); }
}

/* ---- views ---- */
async function viewMetrics() {
  const [sum, met] = await Promise.all([
    query("SELECT metric,value FROM 'bundle/full_dataset_summary.parquet'"),
    query("SELECT section,metric,value FROM 'bundle/full_dataset_metrics.parquet'"),
  ]);
  const m = Object.fromEntries(sum.map((r) => [r.metric, r.value]));
  const qm = Object.fromEntries(met.filter((r) => r.section === "incoming_query").map((r) => [r.metric, r.value]));
  const b3 = m.b3_precision && m.b3_recall ? (2 * m.b3_precision * m.b3_recall) / (m.b3_precision + m.b3_recall) : 0;
  const k = [["candidate recall", m.candidate_pair_recall], ["B³ F1", b3],
    ["top-1 identity", qm.top1_identity_accuracy_existing], ["MRR", qm.mean_reciprocal_rank_existing],
    ["review", qm.review_rate], ["no-match", qm.no_match_rate]];
  const cards = k.map(([l, v]) => `<div class="card"><div class="v">${(v * 100).toFixed(1)}%</div><div class="l">${esc(l)}</div></div>`).join("");
  paint(`<div class="kpi">${cards}</div>
    <div class="card"><h3>エンティティ数</h3><div class="v">${Number(m.predicted_entities ?? 0).toLocaleString()}</div>
    <div class="members">候補エッジ ${Number(m.candidate_edges ?? 0).toLocaleString()} · シングルトン率 ${(m.singleton_entity_rate * 100).toFixed(1)}%</div></div>`);
}

async function searchView() {
  const box = `<div class="card"><h3><label for="q">氏名・カナ・電話・郵便番号で検索</label></h3>
    <input type="search" id="q" name="q" placeholder="例：佐藤 太郎" autocomplete="off" aria-describedby="results">
    <div id="results" role="status" aria-live="polite" class="members"></div></div>
    <div class="card"><h3>照合テスト</h3>
    <div class="lin"><input id="rt-name" class="bar" placeholder="氏名漢字" style="width:12rem">
    <input id="rt-phone" class="bar" placeholder="電話" style="width:12rem">
    <input id="rt-kana" class="bar" placeholder="カナ" style="width:12rem"></div>
    <button id="rt-go" type="button" class="more">照合</button><div id="rt-out" class="members"></div></div>`;
  paint(box);
  let timer;
  $("#q").addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const q = $("#q").value.trim();
      const out = $("#results");
      if (q.length < 2) { out.textContent = ""; return; }
      try {
        const rows = await query(
          `SELECT nayose_id,name_kanji,name_kana,phone,postal_code,record_count FROM 'bundle/canonical_entities.parquet'
           WHERE name_kanji LIKE '%${like(q)}%' OR name_kana LIKE '%${like(q)}%'
             OR phone LIKE '%${like(q)}%' OR postal_code LIKE '%${like(q)}%' LIMIT 50`);
        out.innerHTML = rows.length ? rows.map((r) =>
          `<div class="card"><h3><a href="#/entity/${esc(r.nayose_id)}">${esc(r.name_kanji)}</a> <span class="muted">${esc(r.name_kana || "")}</span></h3>
           <div class="members">${r.record_count} 件 · ${esc(r.phone || "")} · ${esc(r.postal_code || "")}</div></div>`).join("")
          : '<span class="blank">該当なし</span>';
      } catch (e) { out.textContent = "エラー: " + e.message; }
    }, 150);
  });
  $("#rt-go").addEventListener("click", async () => {
    const rec = { name_kanji: $("#rt-name").value, name_kana: $("#rt-kana").value, phone: $("#rt-phone").value };
    const out = $("#rt-out");
    try {
      const res = await fetch("/api/recommend", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_id: state.run, record: rec }) });
      if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
      const d = await res.json();
      out.innerHTML = `${chip(d.action, d.action === "AUTO_LINK" ? "ok" : d.action === "REVIEW" ? "warn" : "bad")} ` +
        `確率 ${(d.top_probability * 100).toFixed(1)}% · ${esc(d.top_nayose_id || "—")} · 候補 ${d.candidates.length} 件` +
        (d.candidates?.length ? `<div class="lin">${d.candidates.map((c) => `<span>${esc(c.name || c.nayose_id)} ${(c.probability * 100).toFixed(0)}%</span>`).join("")}</div>` : "");
    } catch (e) { out.textContent = "エラー: " + e.message; }
  });
}

async function entityView(id) {
  const ent = await query(`SELECT * FROM 'bundle/canonical_entities.parquet' WHERE nayose_id = '${esc(id)}' LIMIT 1`);
  if (!ent.length) return paint(`<div class="empty">エンティティ ${esc(id)} が見つかりません</div>`);
  const e = ent[0];
  const members = await query(
    `SELECT r.record_id,r.name_kana,r.postal_code,r.phone,r.email FROM 'data/reference_records.parquet' r
     JOIN 'bundle/record_to_nayose.parquet' m ON r.record_id = m.record_id
     WHERE m.nayose_id = '${esc(id)}' LIMIT 10`);
  let lin = "";
  if (members.length) {
    const ids = members.map((m) => `'${esc(m.record_id)}'`).join(",");
    const ls = await query(
      `SELECT field,transformation,count(*) AS c FROM 'data/ground_truth/field_lineage.parquet'
       WHERE item_id IN (${ids}) GROUP BY field,transformation ORDER BY field,c DESC`);
    const by = {};
    ls.forEach((r) => (by[r.field] = by[r.field] || []).push(r.transformation));
    lin = Object.entries(by).map(([f, ts]) =>
      `<div class="lin"><span>${esc(f)}</span>${ts.map((t) => `<span>${esc(t)}</span>`).join("")}</div>`).join("");
  }
  paint(`<div class="card"><h2>${esc(e.name_kanji)} <span class="muted">${esc(e.name_kana || "")}</span></h2>
    <div class="members">${esc(e.nayose_id)} · ${e.record_count} 件 · ${esc(e.date_birth || "")} · ${esc(e.address || "")}</div></div>
    ${lin ? `<div class="card"><h3>フィールド由来（lineage）</h3>${lin}</div>` : ""}
    <div class="card"><h3>所属レコード</h3><table><thead><tr><th>record</th><th>カナ</th><th>電話</th></tr></thead>
    <tbody>${members.map((m) => `<tr><td>${esc(m.record_id)}</td><td>${esc(m.name_kana)}</td><td>${esc(m.phone)}</td></tr>`).join("")}</tbody></table></div>`);
}

async function reviewView() {
  let offset = 0;
  async function load() {
    busy("読み込み中…");
    const rows = await query(`SELECT left_record_id,right_record_id,pair_probability,retrieval_score,exact_count
      FROM 'bundle/review_queue.parquet' ORDER BY pair_probability DESC LIMIT 200 OFFSET ${offset}`);
    const rowsHtml = rows.map((r) =>
      `<tr><td><a href="#/compare/${esc(r.left_record_id)}/${esc(r.right_record_id)}">${esc(r.left_record_id)}</a></td>
       <td><a href="#/compare/${esc(r.left_record_id)}/${esc(r.right_record_id)}">${esc(r.right_record_id)}</a></td>
       <td>${(r.pair_probability * 100).toFixed(1)}%</td><td>${r.exact_count}</td></tr>`).join("");
    paint(`<div class="card"><h3>レビュー待機エッジ <span class="muted">（${offset + 200} 件まで表示）</span></h3>
      <table><thead><tr><th>左レコード</th><th>右レコード</th><th>確率</th><th>exact</th></tr></thead>
      <tbody>${rowsHtml}</tbody></table>
      <button type="button" class="more" id="more">次の 200 件</button></div>`);
    $("#more").addEventListener("click", () => { offset += 200; load(); });
  }
  await load();
}

async function confusionView() {
  const [pc, qc] = await Promise.all([
    query("SELECT metric,value FROM 'bundle/pair_confusion_test.parquet'"),
    query("SELECT scenario,action,expected_yes FROM 'bundle/query_confusion.parquet'"),
  ]);
  const pm = Object.fromEntries(pc.map((r) => [r.metric, r.value]));
  const actions = ["AUTO_LINK", "REVIEW", "NO_MATCH", "INSUFFICIENT_INPUT"];
  const grid = {};
  qc.forEach((r) => { (grid[r.scenario] = grid[r.scenario] || {})[r.action] = r.expected_yes; });
  const scenRows = Object.entries(grid).map(([s, a]) =>
    `<tr><th scope="row">${esc(s)}</th>${actions.map((x) => `<td>${a[x] ?? 0}</td>`).join("")}</tr>`).join("");
  const pairRows = ["true_positives", "false_positives", "true_negatives", "false_negatives", "precision", "recall", "specificity", "f1", "auc"]
    .filter((m) => m in pm).map((m) => `<tr><th scope="row">${esc(m)}</th><td>${Number(pm[m]).toFixed(4)}</td></tr>`).join("");
  paint(`<div class="card"><h3>ペア検証器 — 未見テスト分割</h3>
    <table><tbody>${pairRows}</tbody></table></div>
    <div class="card"><h3>クエリ判定（scenario × action · 期待既存顧客数）</h3>
    <table><thead><tr><th>scenario</th>${actions.map((a) => `<th>${esc(a)}</th>`).join("")}</tr></thead>
    <tbody>${scenRows}</tbody></table></div>`);
}

async function compareView(l, r) {
  const rows = await query(
    `SELECT * FROM 'data/reference_records.parquet' WHERE record_id IN ('${esc(l)}','${esc(r)}')`);
  const byId = Object.fromEntries(rows.map((x) => [x.record_id, x]));
  const left = byId[l], right = byId[r];
  if (!left || !right) return paint(`<div class="empty">レコードが見つかりません</div>`);
  const fields = Object.keys(left).filter((f) => !["record_id", "policy_id", "observed_at"].includes(f));
  const rowHtml = (rec, other) => fields.map((f) => {
    const a = esc(rec[f]), b = esc(other[f]);
    return `<tr><th scope="row">${esc(f)}</th><td class="${a !== b ? "diff-hi" : ""}">${a}</td></tr>`;
  }).join("");
  paint(`<div class="diff">
    <div class="card"><h3>${esc(l)}</h3><table><tbody>${rowHtml(left, right)}</tbody></table></div>
    <div class="card"><h3>${esc(r)}</h3><table><tbody>${rowHtml(right, left)}</tbody></table></div></div>
    <div class="card members">差分行はハイライト表示。</div>`);
}

async function entitiesView() {
  const rows = await query(`SELECT nayose_id,name_kanji,name_kana,record_count FROM 'bundle/canonical_entities.parquet'
    ORDER BY record_count DESC LIMIT 100`);
  paint(`<div class="card"><h3>エンティティ一覧（多レコード順・先頭100件）</h3>
    ${rows.map((r) => `<div class="card" style="margin:.4rem 0"><h3><a href="#/entity/${esc(r.nayose_id)}">${esc(r.name_kanji)}</a>
    <span class="muted">${esc(r.name_kana || "")}</span></h3><div class="members">${r.record_count} 件</div></div>`).join("") || '<div class="empty">ありません</div>'}
    </div>`);
}

/* ---- router (URL as state) ---- */
const routes = {
  "": viewMetrics, metrics: viewMetrics, search: searchView, review: reviewView,
  confusion: confusionView, entities: entitiesView,
  entity: (m) => entityView(decodeURIComponent(m[1])),
  compare: (m) => compareView(decodeURIComponent(m[1]), decodeURIComponent(m[2])),
};
async function navigate() {
  const hash = location.hash.replace(/^#\/?/, "");
  const parts = hash.split("/");
  const fn = routes[parts[0]];
  document.querySelectorAll(".side a").forEach((a) =>
    a.setAttribute("aria-current", a.dataset.nav === (parts[0] || "metrics") ? "page" : ""));
  window.scrollTo(0, 0);
  await run(() => fn ? fn(parts) : viewMetrics());
}
window.addEventListener("hashchange", navigate);

/* ---- run selector + theme + palette ---- */
async function initRuns() {
  const res = await fetch("/api/runs");
  const runs = await res.json();
  const sel = $("#run");
  sel.innerHTML = runs.map((r) => `<option value="${esc(r.id)}" ${r.current ? "selected" : ""}>${esc(r.id)}${r.current ? " ★" : ""}</option>`).join("");
  if (runs.length && !runs.some((r) => r.id === state.run)) sel.selectedIndex = runs.findIndex((r) => r.current) >= 0 ? runs.findIndex((r) => r.current) : 0;
  state.run = sel.value;
  localStorage.setItem("nayose.run", state.run);
  sel.addEventListener("change", () => { state.run = sel.value; localStorage.setItem("nayose.run", state.run); navigate(); });
}
$("#theme").addEventListener("click", () => {
  state.theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = state.theme;
  localStorage.setItem("nayose.theme", state.theme);
});

const palette = $("#palette"), box = $("#palette-input"), results = $("#palette-results");
let paletteTimer;
function openPalette() { palette.hidden = false; palette.classList.add("open"); box.value = ""; box.focus(); }
function closePalette() { palette.classList.remove("open"); palette.hidden = true; }
async function paletteSearch(q) {
  results.innerHTML = "";
  const nav = [["metrics", "指標"], ["search", "検索"], ["review", "レビュー待機"], ["confusion", "混同行列"], ["entities", "エンティティ"]];
  const items = nav.map(([id, label]) => ({ html: `<span>${label}</span><span class="muted">#/${id}</span>`, go: () => { closePalette(); location.hash = "#/" + id; } }));
  if (q.length >= 2) {
    try {
      const rows = await query(`SELECT nayose_id,name_kanji,name_kana FROM 'bundle/canonical_entities.parquet'
        WHERE name_kanji LIKE '%${like(q)}%' OR name_kana LIKE '%${like(q)}%' OR nayose_id LIKE '%${like(q)}%' LIMIT 12`);
      rows.forEach((r) => items.push({ html: `<span>${esc(r.name_kanji)} ${esc(r.name_kana || "")}</span><span class="muted">${esc(r.nayose_id)}</span>`,
        go: () => { closePalette(); location.hash = "#/entity/" + r.nayose_id; } }));
    } catch (e) { /* palette best-effort */ }
  }
  items.slice(0, 12).forEach((it, i) => {
    const li = document.createElement("li");
    li.innerHTML = it.html;
    li.setAttribute("role", "option");
    li.addEventListener("click", it.go);
    li.dataset.i = i;
    results.appendChild(li);
  });
}
box.addEventListener("input", () => { clearTimeout(paletteTimer); paletteTimer = setTimeout(() => paletteSearch(box.value), 150); });
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); }
  if (e.key === "Escape") closePalette();
});
palette.addEventListener("click", (e) => { if (e.target === palette) closePalette(); });

/* ---- boot ---- */
initRuns().then(navigate);