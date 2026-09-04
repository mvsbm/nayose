// Functional tests: boot the app with a MOCK QueryPort (no network, no SQL) —
// views are exercised through their public behavior in a real DOM (jsdom).
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dirname, "..");

const ENTITIES = [
  { nayose_id: "N000000001", name_kanji: "佐藤 太郎", name_kana: "サトウ タロウ", phone: "090-1234-5678", postal_code: "1000001", record_count: 2 },
  { nayose_id: "N000000002", name_kanji: "鈴木 花子", name_kana: "スズキ ハナコ", phone: "080-0000-0000", postal_code: "1500001", record_count: 1 },
];
const REVIEW = [
  { left_record_id: "R0000000000", right_record_id: "R0000000001", pair_probability: 0.81, exact_count: 1 },
  { left_record_id: "R0000000002", right_record_id: "R0000000003", pair_probability: 0.22, exact_count: 0 },
];

const mockPort = {
  _run: "",
  run(id) { this._run = id; },
  entities(q) {
    const filtered = q ? ENTITIES.filter((e) => JSON.stringify(e).includes(q)) : ENTITIES;
    return Promise.resolve(filtered);
  },
  entity(id) {
    const e = ENTITIES.find((x) => x.nayose_id === id);
    return Promise.resolve({ entity: e ?? {}, members: [], lineage: { name_kanji: ["current", "homophone_kanji"] } });
  },
  review(minP, offset, limit) {
    const rows = REVIEW.filter((r) => r.pair_probability >= minP).slice(offset, offset + limit);
    return Promise.resolve({ total: rows.length, rows });
  },
  metrics() {
    return Promise.resolve({ kpis: [["candidate recall", 0.99], ["B3 F1", 0.96]], extras: { entities: 2, edges: 3, singleton: 0.2 } });
  },
  metricsFor(run) {
    return Promise.resolve({ kpis: [["candidate recall", run === "runB" ? 0.95 : 0.99], ["B3 F1", 0.96]], extras: {} });
  },
  savedDecisions: [],
  loadDecisions() {
    return Promise.resolve(this.savedDecisions);
  },
  async saveDecisions(items) {
    this.savedDecisions = items;
  },
  confusion() {
    return Promise.resolve({
      pairs: { true_positives: 1, precision: 0.99 },
      scenarios: [{ scenario: "existing_stable", actions: { AUTO_LINK: 5, REVIEW: 1 } }],
      decisions: { existing_stable: [{ query_id: "Q1", action: "AUTO_LINK", top_nayose_id: "N1", top_probability: 0.9, identity_top1_correct: 1 }] },
    });
  },
  compare(l, r) {
    return Promise.resolve([
      { record_id: l, name_kanji: "佐藤 太郎", phone: "090-1" },
      { record_id: r, name_kanji: "佐藤 太郎", phone: "090-2" },
    ]);
  },
  recommend(record) {
    return Promise.resolve({ action: "AUTO_LINK", top_nayose_id: "N000000001", top_probability: 0.92, margin: 0.1, candidates: [] });
  },
};

let dom;
beforeEach(async () => {
  dom = new JSDOM(readFileSync(join(ROOT, "index.html"), "utf8"), { url: "http://localhost/", pretendToBeVisual: true });
  const { window } = dom;
  globalThis.window = window;
  globalThis.document = window.document;
  globalThis.location = window.location;
  globalThis.localStorage = window.localStorage;
  window.scrollTo = () => {};
  window.HTMLElement.prototype.scrollIntoView = () => {};
});

let bootSeq = 0;
async function boot() {
  const { boot } = await import(join(ROOT, "js/app.js") + "?boot=" + bootSeq++);
  await boot({ port: mockPort, runs: [{ id: "run_fake", current: true, fingerprint: "abc" }] });
  await new Promise((r) => setTimeout(r, 60));
}
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

test("default route = search (task-first), run selector populated", async () => {
  await boot();
  assert.ok(document.querySelector("#q"), "search input on default route");
  assert.equal(document.querySelector("#run").value, "run_fake");
  assert.match(document.querySelector("#run-fp").textContent, /abc/);
});

test("search: typing renders entity cards + terse live status; links to entity route", async () => {
  await boot();
  const input = document.querySelector("#q");
  input.value = "佐藤";
  input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  await wait(400);
  const hits = [...document.querySelectorAll("#results .hit")];
  assert.equal(hits.length, 1);
  assert.equal(hits[0].getAttribute("href"), "#/entity/N000000001");
  assert.equal(document.querySelector("#stat").textContent, "1 件");
});

test("palette: ⌘K opens, keyboard focus reaches entity, Enter navigates", async () => {
  await boot();
  document.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "k", metaKey: true, bubbles: true }));
  assert.ok(!document.querySelector("#palette").hidden);
  const input = document.querySelector("#palette-input");
  input.value = "佐藤";
  input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  await wait(200);
  const pick = () => document.querySelector('#palette-results li[aria-selected="true"]');
  let guard = 0;
  while (guard++ < 8 && !(pick()?.textContent ?? "").includes("N000000001")) {
    input.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
  }
  assert.ok((pick()?.textContent ?? "").includes("N000000001"));
  input.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  await wait(30);
  assert.ok(window.location.hash.startsWith("#/entity/"));
});

test("review: rows render, 既読 toggles store + localStorage, 取り消し undoes (Command)", async () => {
  await boot();
  window.location.hash = "#/review";
  await wait(100);
  const rows = [...document.querySelectorAll("#view tbody tr")];
  assert.equal(rows.length, 2);
  rows[0].querySelector("button.check").click();
  await wait(20);
  const stored = JSON.parse(localStorage.getItem("nayose.checked") ?? "[]");
  assert.equal(stored.length, 1);
  document.querySelector("#undo").click();
  await wait(20);
  const after = JSON.parse(localStorage.getItem("nayose.checked") ?? "[]");
  assert.equal(after.length, 0, "undo restored");
});

test("confusion: scenario row renders; click drills decisions", async () => {
  await boot();
  window.location.hash = "#/confusion";
  await wait(100);
  const scen = document.querySelector("#view tr.scen");
  assert.ok(scen, "scenario row present");
  scen.click();
  await wait(20);
  assert.ok(document.querySelector("#drill tbody tr"), "decision drill rendered");
});



test("server decisions restore checked state at boot (cross-machine persistence)", async () => {
  mockPort.savedDecisions = [{ left: "R0000000000", right: "R0000000001" }];
  await boot();
  window.location.hash = "#/review";
  await wait(100);
  const firstRow = document.querySelector("#view tbody tr");
  assert.ok(firstRow.classList.contains("checked"), "restored decision marked checked");
});

test("cross-run diff view renders metrics delta table", async () => {
  await boot();
  window.location.hash = "#/compare-runs";
  await wait(120);
  const go = document.querySelector("#go");
  assert.ok(go, "compare panel present");
  go.click();
  await wait(120);
  const table = document.querySelector("#out table");
  assert.ok(table, "diff table rendered");
  assert.match(table.textContent, /candidate recall/);
  assert.match(table.textContent, /−|\+|%/);
});

test("metrics: KPI cards + bars render from port", async () => {
  await boot();
  window.location.hash = "#/metrics";
  await wait(100);
  const cards = [...document.querySelectorAll("#view .kpi .card")];
  assert.equal(cards.length, 2);
  assert.ok(cards[0].querySelector(".bar i"), "bar present");
});