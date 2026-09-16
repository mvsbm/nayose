// Functional tests: boot the app with a mock WorkbenchPort (no network, no SQL) —
// views are exercised through their public behavior in a real DOM (jsdom).
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dirname, "..");

const ENTITIES = [
  { nayose_id: "N000000001", name_kanji: "佐藤 太郎", name_kana: "サトウ タロウ", phone: "090-1234-5678", postal_code: "1000001", record_count: 2, score: 0.91 },
  { nayose_id: "N000000002", name_kanji: "鈴木 花子", name_kana: "スズキ ハナコ", phone: "080-0000-0000", postal_code: "1500001", record_count: 1 },
];
const REVIEW = [
  { left_record_id: "R0000000000", right_record_id: "R0000000001", pair_probability: 0.81, exact_count: 1 },
  { left_record_id: "R0000000002", right_record_id: "R0000000003", pair_probability: 0.22, exact_count: 0 },
];
const EVENT_IDS = ["rename_stale", "move_stale", "contact_change", "full_snapshot", "contact_omission", "paper_ocr", "legacy_format", "doc_damage", "wrong_party_type", "alpha_in_kana", "combined_kana", "kana_in_romaji", "homophone_kanji", "kana_form", "digit_transpose", "email_typo", "shared_contact"];
const NOISE_FIELDS = ["name_kanji", "name_kana", "name_romaji", "date_birth", "postal_code", "address", "phone", "email"];
const CHANNEL_IDS = ["web", "agent_pc", "paper_ocr", "legacy_migration"];
const GENERATION_CATALOG = {
  profiles: [{ id: "preflight", label_ja: "事前確認", label_en: "Preflight", description_ja: "small", description_en: "small", n_parties: 300, mean_records: 2.4 }, { id: "smoke", label_ja: "スモーク", label_en: "Smoke", description_ja: "small", description_en: "small", n_parties: 700, mean_records: 2.5 }, { id: "mac", label_ja: "Mac 標準", label_en: "Mac standard", description_ja: "mac", description_en: "mac", n_parties: 6000, mean_records: 2.7 }],
  difficulties: [{ id: "debug", label_ja: "デバッグ", label_en: "Debug", description_ja: "debug", description_en: "debug", poor: .10, catastrophic: .005, quality_share: { average: .90, poor: .095, catastrophic: .005 } }, { id: "realistic", label_ja: "現実的", label_en: "Realistic", description_ja: "realistic", description_en: "realistic", poor: .33, catastrophic: .04, quality_share: { average: .67, poor: .29, catastrophic: .04 } }, { id: "severe", label_ja: "厳しい", label_en: "Severe", description_ja: "severe", description_en: "severe", poor: .43, catastrophic: .07, quality_share: { average: .57, poor: .36, catastrophic: .07 } }],
  events: EVENT_IDS.map(id => ({ id, group: "test", label_ja: id, label_en: id, description_ja: "example", description_en: "example", example_ja: "A → B", example_en: "A → B", fields: ["name_kanji"], default_rate: .05 })),
  channels: CHANNEL_IDS.map(id => ({ id, availability: Object.fromEntries(NOISE_FIELDS.map(field => [field, 1])), bundle: [] })),
  query_scenarios: ["existing_stable", "existing_changed", "existing_damaged", "new_customer", "related_only", "insufficient"].map(id => ({ id, label_ja: id, label_en: id, description_ja: "example", description_en: "example", default_share: 1 / 6 })),
  defaults: {
    profile: "smoke", difficulty: "realistic", seed: 42, n_parties: 0, mean_records: 2.5, n_agencies: 18, person_share: .9, query_share: .16, twin_rate: .15, poor: .33, catastrophic: .04, snapshot_date: "2025-12-31", query_date: "2026-08-25",
    event_rates: Object.fromEntries(EVENT_IDS.map(id => [id, .05])), band_scale: { average: .35, poor: .75, catastrophic: 1.6 }, missing_rates: { average: .05, poor: .18, catastrophic: .5 }, field_availability: Object.fromEntries(CHANNEL_IDS.map(id => [id, Object.fromEntries(NOISE_FIELDS.map(field => [field, 1]))])), stale_rate: .09, query_mix: Object.fromEntries(["existing_stable", "existing_changed", "existing_damaged", "new_customer", "related_only", "insufficient"].map(id => [id, 1 / 6])),
  },
  bounds: { rate: { min: 0, max: 1 }, band_scale: { min: 0, max: 3 } },
};
function mockGenerationPreview(controls) {
  const n = controls.n_parties || 700;
  const mean = controls.mean_records || 2.5;
  const queries = Math.round(n * controls.query_share);
  const sample = (quality) => ({ quality, channel: "paper_ocr", canonical: { party_type: "person", name_kanji: "佐藤 太郎", name_kana: "サトウ タロウ", name_romaji: "Sato Taro", date_birth: "1988-04-12", postal_code: "100-0001", address: "東京都千代田区千代田1-2-3", phone: "090-1234-5678", email: "sato.taro@example.jp" }, observed: { party_type: "person", name_kanji: "佐藤 太郎", name_kana: "サトウ 太郎", name_romaji: "Sato Taro", date_birth: "1988-04-12", postal_code: "100-0001", address: "東京都千代田区千代田1-2-3", phone: "090-1234-5678", email: "" }, changes: [{ field: "email", before: "sato.taro@example.jp", after: "", transformations: ["missing"] }] });
  return { config_hash: "preview123456", controls, time_window: { snapshot_date: controls.snapshot_date, query_date: controls.query_date, gap_days: 237 }, dataset_impact: { quality: [{ id: "average", share: .67, records: Math.round(n * mean * .67), corruption_scale: .35, residual_missing_rate: .05, estimated_missing_records: 1 }, { id: "poor", share: .29, records: Math.round(n * mean * .29), corruption_scale: .75, residual_missing_rate: .18, estimated_missing_records: 1 }, { id: "catastrophic", share: .04, records: Math.round(n * mean * .04), corruption_scale: 1.6, residual_missing_rate: .5, estimated_missing_records: 1 }], channels: CHANNEL_IDS.map(id => ({ id, share: 1 / CHANNEL_IDS.length, records: Math.round(n * mean / CHANNEL_IDS.length), bundle: [], availability: Object.fromEntries(NOISE_FIELDS.map(field => [field, 1])) })), fields: NOISE_FIELDS.map(id => ({ id, estimated_missing_rate: .1, estimated_missing_records: 1 })), overall: { weighted_corruption_scale: .5, weighted_residual_missing_rate: .1, field_rate_note: "test" } }, counts: { parties: n, records: Math.round(n * mean), queries, agencies: controls.n_agencies }, quality: { average: .67, poor: .29, catastrophic: .04 }, weighted_band_scale: .5, events: GENERATION_CATALOG.events.map(e => ({ ...e, rate: controls.event_rates[e.id], effective_rate: controls.event_rates[e.id], estimated_records: Math.round(n * mean * controls.event_rates[e.id]) })), missingness: { by_quality: controls.missing_rates, estimated_average_rate: .1 }, channels: GENERATION_CATALOG.channels, samples: { average: sample("average"), poor: sample("poor"), catastrophic: sample("catastrophic") }, examples: [sample("average"), sample("poor"), sample("catastrophic")], query_mix: GENERATION_CATALOG.query_scenarios.map(s => ({ ...s, share: controls.query_mix[s.id], estimated_queries: Math.round(queries * controls.query_mix[s.id]) })), warnings: [], estimate_note: { ja: "期待値", en: "Estimated counts" } };
}

const mockPort = {
  _run: "",
  run(id) { this._run = id; },
  runs() {
    return Promise.resolve([{ id: "run_fake", current: true, fingerprint: "abc" }]);
  },
  entities(q, limit) {
    const filtered = q ? ENTITIES.filter((e) => JSON.stringify(e).includes(q)) : ENTITIES;
    if (q && q.startsWith("z")) {
      return Promise.resolve({ rows: [], exact: [], semantic_results: [], nearest: [{ ...ENTITIES[0], kind: "nearest", score: 0.76, match_method: "fuzzy_nearest", match_field: "name_kanji", signals: ["name_kanji"] }], semantic: false, mode: "exact_plus_fuzzy", suggestion: { term: "佐藤 太郎", score: 0.8, method: "fuzzy_nearest", field: "name_kanji" } });
    }
    return Promise.resolve({ rows: filtered.slice(0, limit ?? filtered.length), semantic: true, suggestion: null });
  },
  entityExplorer(q, offset, limit, sort, filters) {
    let rows = q ? ENTITIES.filter((e) => JSON.stringify(e).includes(q)) : ENTITIES;
    if (filters?.party_type) rows = rows.filter((e) => e.party_type === filters.party_type);
    if (filters?.min_record_count) rows = rows.filter((e) => e.record_count >= filters.min_record_count);
    if (sort === "name") rows = [...rows].sort((a, b) => a.name_kanji.localeCompare(b.name_kanji));
    if (sort === "records") rows = [...rows].sort((a, b) => b.record_count - a.record_count);
    return Promise.resolve({ rows: rows.slice(offset, offset + limit), total: rows.length, offset, limit, query: q, sort, filters: filters ?? {}, query_plan: {}, search_mode: "exact", total_is_exact: true, search_trace: {} });
  },
  entityGraph(id, includePotential) {
    const entity = ENTITIES.find((e) => e.nayose_id === id);
    const entityNode = { id: `entity:${id}`, kind: "entity", label: entity?.name_kanji ?? id, metadata: { nayose_id: id } };
    const recordNode = { id: "record:R0", kind: "record", label: "R0", metadata: {} };
    const edges = [{ source: entityNode.id, target: recordNode.id, kind: "observed", relation: "member_record", evidence: { source: "reference_records", record_id: "R0" } }];
    return Promise.resolve({ entity_id: id, nodes: [entityNode, recordNode], edges, truncated: false, potential_matches_available: Boolean(includePotential) });
  },
  normalizePreview(text) {
    const t = text.replace(/\s/g, "");
    return Promise.resolve({ name_kanji: t, name_kana: "", phone: "", postal_code: "" });
  },
  entity(id, offset = 0, limit = 25) {
    const e = ENTITIES.find((x) => x.nayose_id === id);
    const members = Array.from({ length: 30 }, (_, i) => ({ record_id: `R${i}`, name_kanji: "佐藤 太郎", name_kana: "サトウ タロウ", phone: "090-1234-5678", address: "東京都", email: "", channel: "paper_ocr", policy_id: `P${i}`, agency_id: "A1", observed_at: "2020-01-01" })).slice(offset, offset + limit);
    return Promise.resolve({ entity: e ?? {}, members, total: 30, offset, limit, comparisons: {}, merge_evidence_available: false });
  },
  matchSample() {
    return Promise.resolve({ record: { name_kanji: "佐藤 太郎", phone: "090-1234-5678", record_id: "R0" } });
  },
  health() {
    return Promise.resolve({
      status: "ok",
      run: "run_fake",
      fingerprint: "abc",
      engine_warm: true,
      stats: [],
      now_utc: "2026-01-01T00:00:00",
      resources: {
        operating_system: "Darwin",
        machine: "arm64",
        cpu_count: 10,
        available_cpus: 10,
        memory: { available_bytes: 8 * 1024 ** 3, total_bytes: 16 * 1024 ** 3, process_rss_bytes: 2 * 1024 ** 3, cgroup_limit_bytes: null },
        storage: { path: "/data", available_bytes: 50 * 1024 ** 3, total_bytes: 100 * 1024 ** 3, reserve_bytes: 20 * 1024 ** 3 },
        policy: { device: "mps", process_workers: 1, cpu_threads: null, model_threads: null, embedding_batch_size: 32 },
      },
      configuration: { hash: "config123456789", schema_version: 1, source: "nayose.toml + environment overrides" },
    });
  },
  adminCapability() {
    return Promise.resolve({ capable: true });
  },
  adminConfig() {
    return Promise.resolve({ schema_version: 1, config_hash: "config123", source: "nayose.toml + environment overrides", editable: false, sections: { paths: { data_dir: "data/current" }, serving: { port: 8000, exact_mode: "hybrid" }, embedding: { model_id: "F2LLM" }, performance: { model_threads: null }, retrieval: {}, inference: {}, training: { model_family: "off", device: "mps", seed: 42, max_full_match_records: 100000, candidate_cap_per_record: 80 }, generation: { profile: "smoke" }, sources: {}, release: {}, tooling: {} } });
  },
  adminState() {
    return Promise.resolve({
      profile: { profiles: ["smoke", "mac"], difficulties: ["realistic"], model_families: ["off"], devices: ["mps"], exact_modes: ["hybrid", "exact_only", "fuzzy_only"], threads_default: 2 },
      settings: { exact_mode: "hybrid", warm_embed: false, engine_warm: true },
      runs: [{ id: "run_fake", manifest: true, current: true }],
      manual_count: 0,
      audit: [{ ts: "2026-01-01", actor: "pytest", action: "boot" }],
    });
  },
  adminGenerationConfig() { return Promise.resolve(GENERATION_CATALOG); },
  adminGenerations() { return Promise.resolve([{ id: "gen_fake", current: true, created_at: "2026-01-01", fingerprint: "abc123", snapshot_date: "2025-12-31", query_date: "2026-08-25", counts: { parties: 2, observations: 3, queries: 1 } }]); },
  adminDatasetSummary() { return Promise.resolve({ id: "gen_fake", current: true, created_at: "2026-01-01", fingerprint: "abc123", config: {}, counts: { parties: 2, observations: 3, queries: 1 }, tables: [{ id: "reference", label: "Reference records", rows: 3, columns: ["record_id", "name_kanji"] }, { id: "queries", label: "Incoming queries", rows: 1, columns: ["query_id", "name_kanji"] }, { id: "agencies", label: "Agencies", rows: 1, columns: ["agency_id", "channel"] }] }); },
  adminDatasetRows() { return Promise.resolve({ table: "reference", columns: ["record_id", "name_kanji"], rows: [{ record_id: "R0", name_kanji: "佐藤 太郎" }], total: 1, offset: 0, limit: 25 }); },
  adminGenerationPreview(controls) { this.lastGenerationPreview = controls; return Promise.resolve(mockGenerationPreview(controls)); },
  adminStart(action, params) {
    this.lastAdminStart = { action, params };
    this.calls = this.calls ?? {};
    this.calls.admin = (this.calls.admin ?? 0) + 1;
    return Promise.resolve({ id: "ajob", run_id: "admin", stage: "complete", elapsed_ms: 2, timeline: [{ stage: "queued", elapsed_ms: 0 }, { stage: "complete", elapsed_ms: 2 }], error: null, result: { run: "run_new", fingerprint: "abc" } });
  },
  adminJob(id) {
    this.adminStep = (this.adminStep ?? 0) + 1;
    if (this.adminStep === 1) {
      return Promise.resolve({ id, run_id: "admin", stage: "generating", elapsed_ms: 3, timeline: [], progress: { phase: "observations", pct: 42, eta_seconds: 90, done: 5, total: 5 }, error: null, result: null });
    }
    return Promise.resolve({ id, run_id: "admin", stage: "complete", elapsed_ms: 2, timeline: [], progress: { phase: "observations", pct: 100, eta_seconds: 0, done: 5, total: 5 }, error: null, result: { run: "run_new", fingerprint: "abc" } });
  },
  adminAction(action, params) {
    if (action === "settings" && params.exact_mode === "exact_only") this.exactMode = "exact_only";
    return Promise.resolve({ ok: true });
  },
  manualList() {
    return Promise.resolve({ count: this.manualRows?.length ?? 0, rows: this.manualRows ?? [] });
  },
  manualRegister(record) {
    this.manualRows = [record];
    return Promise.resolve({ ok: true, record_id: "M0000000001" });
  },
  manualImport(rows, dryRun) {
    return Promise.resolve({ ok: true, dry_run: dryRun, valid: rows.length, errors: [], duplicates: [], imported: dryRun ? 0 : rows.length });
  },
  manualUndo(ids) {
    this.manualRows = [];
    return Promise.resolve({ ok: true, removed: ids.length });
  },
  startRecommendation(record, thresholds) {
    return Promise.resolve({ id: "job1", run_id: "run_fake", stage: "complete", elapsed_ms: 10, timeline: [{ stage: "complete", elapsed_ms: 10 }], error: null, result: MATCH_RESULT });
  },
  recommendation(id) {
    return this.startRecommendation({});
  },
  review(minP, offset, limit, nayose) {
    const rows = REVIEW.filter((r) => r.pair_probability >= minP).slice(offset, offset + limit);
    return Promise.resolve({ total: rows.length, rows, histogram: [[0, 2]], pair_auto_threshold: 0.5 });
  },
  metrics() {
    return Promise.resolve({ kpis: [["candidate recall", 0.99], ["B3 F1", 0.96]], extras: { entities: 2, edges: 3, singleton: 0.2 } });
  },
  metricsFor(run) {
    return Promise.resolve({ kpis: [["candidate recall", run === "runB" ? 0.95 : 0.99], ["B3 F1", 0.96]], extras: {} });
  },
  savedDecisions: [],
  decisionRevision: 0,
  loadDecisions() {
    return Promise.resolve({ items: this.savedDecisions, revision: this.decisionRevision });
  },
  async saveDecisions(items, revision = this.decisionRevision) {
    if (revision !== this.decisionRevision) throw new Error("decision_conflict");
    this.savedDecisions = items;
    this.decisionRevision += 1;
    return this.decisionRevision;
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

const MATCH_RESULT = {
  action: "REVIEW", reason: "above_review_threshold", top_nayose_id: "N000000001", top_probability: 0.8, margin: 0.02,
  thresholds: { auto: 0.95, review: 0.6 }, scoring_mode: "calibrated_pair_mean_top3", diagnostics: { retrieved: 10, entities: 2, reranker: false, retrieval_backend: "tfidf_svd", exact_mode: "hybrid" },
  candidates: [{ rank: 1, nayose_id: "N000000001", name: "佐藤 太郎", probability: 0.8, support: 1, evidence: [{ record: { record_id: "R0", channel: "paper_ocr" }, score: 0.8, retrieval: { exact_blocks: ["name_kana"], sparse_score: 0.84, dense_score: 0, retrieval_score: 0.92 }, features: { phone_exact: 1 }, comparisons: { phone: { left: "09012345678", right: "090-1234-5678", left_normalized: "09012345678", right_normalized: "09012345678", status: "normalized" }, address: { left: "東京", right: "大阪", left_normalized: "東京", right_normalized: "大阪", status: "different" } } }] }],
  pipeline: { selected_candidate: "N000000001", nodes: [
    { id: "input", kind: "input", status: "complete", metrics: { populated_fields: 2 }, details: { fields: { name_kanji: "佐藤 太朗", phone: "09012345678" } } },
    { id: "normalize", kind: "normalize", status: "complete", metrics: { fields: 2 }, details: { fields: { name_kanji: "佐藤 太朗", phone: "09012345678" } } },
    { id: "retrieve", kind: "retrieve", status: "complete", metrics: { records: 10, entities: 2, backend: "tfidf_svd", mode: "hybrid" }, details: { candidate: "N000000001" } },
    { id: "exact", kind: "exact", status: "complete", metrics: { active: true }, details: { exact_blocks: ["name_kana"], score: 0 } },
    { id: "sparse", kind: "sparse", status: "complete", metrics: { active: true }, details: { score: 0.84 } },
    { id: "dense", kind: "dense", status: "skipped", metrics: { active: false }, details: { score: 0 } },
    { id: "candidate", kind: "candidate", status: "complete", metrics: { rank: 1 }, details: { id: "N000000001", name: "佐藤 太郎" } },
    { id: "compare", kind: "compare", status: "complete", metrics: { exact: 0, normalized: 1, different: 1, missing: 0 }, details: { fields: {} } },
    { id: "score", kind: "score", status: "complete", metrics: { records: 1 }, details: { scores: [0.8] } },
    { id: "aggregate", kind: "aggregate", status: "complete", metrics: { probability: 0.8 }, details: { method: "mean_top_3", scores: [0.8] } },
    { id: "decision", kind: "decision", status: "complete", metrics: { action: "REVIEW" }, details: { reason: "above_review_threshold", thresholds: { auto: 0.95, review: 0.6 }, margin: 0.02 } },
  ], edges: [] },
};

let dom;
let activeDestroy;
beforeEach(async () => {
  activeDestroy?.();
  activeDestroy = undefined;
  dom = new JSDOM(readFileSync(join(ROOT, "index.html"), "utf8"), { url: "http://localhost/", pretendToBeVisual: true });
  const { window } = dom;
  globalThis.window = window;
  globalThis.document = window.document;
  globalThis.location = window.location;
  globalThis.localStorage = window.localStorage;
  globalThis.sessionStorage = window.sessionStorage;
  window.scrollTo = () => {};
  window.HTMLElement.prototype.scrollIntoView = () => {};
});

let bootSeq = 0;
async function boot(opts = {}) {
  const { boot } = await import(join(ROOT, "js/app.js") + "?boot=" + bootSeq++);
  const runs = opts.runs ?? [{ id: "run_fake", current: true, fingerprint: "abc" }];
  const app = await boot({ port: opts.port ?? mockPort, store: opts.store, runs, healthIntervalMs: 0 });
  activeDestroy = app.destroy;
  await new Promise((r) => setTimeout(r, 60));
}
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

test("default route = search (task-first), run selector populated", async () => {
  await boot();
  assert.ok(document.querySelector("#q"), "search input on default route");
  assert.equal(document.querySelector("#run").value, "run_fake");
  assert.match(document.querySelector("#run-fp").textContent, /abc/);
});

test("navigation shell uses one route-driven sidebar and persists its state", async () => {
  await boot();
  const shell = document.querySelector(".shell");
  const toggle = document.querySelector("#nav-toggle");
  const nav = document.querySelector("#primary-navigation");
  const resizer = document.querySelector("#nav-resizer");
  assert.ok(document.querySelector(".app-bar > #nav-toggle"), "navigation control is missing from the toolbar");
  assert.equal(document.querySelector("#activity-navigation"), null, "redundant activity navigation must not return");
  assert.equal(document.querySelector("#status-strip").hidden, false, "status bar starts present");
  assert.equal(toggle.getAttribute("aria-expanded"), "true");
  assert.equal(toggle.dataset.navCollapsed, "false");
  assert.equal(resizer.hidden, false);
  assert.equal(resizer.getAttribute("aria-valuenow"), "256");
  resizer.dispatchEvent(new window.KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
  assert.equal(resizer.getAttribute("aria-valuenow"), "272");
  assert.equal(localStorage.getItem("nayose.navigationWidth"), "272");
  resizer.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Home", bubbles: true }));
  assert.equal(resizer.getAttribute("aria-valuenow"), "220");
  resizer.dispatchEvent(new window.KeyboardEvent("keydown", { key: "End", bubbles: true }));
  assert.equal(resizer.getAttribute("aria-valuenow"), "420");
  assert.equal(nav.hasAttribute("inert"), false);
  assert.equal(document.querySelector('.side a[aria-current="page"]').dataset.nav, "search");

  toggle.click();
  assert.equal(shell.dataset.navCollapsed, "true");
  assert.equal(toggle.getAttribute("aria-expanded"), "false");
  assert.equal(toggle.dataset.navCollapsed, "true");
  assert.equal(nav.hasAttribute("inert"), true);
  assert.equal(localStorage.getItem("nayose.navigationCollapsed"), "true");

  toggle.click();
  assert.equal(shell.dataset.navCollapsed, "false", "toolbar control did not restore the sidebar");
  document.querySelector('.side a[data-nav="review"]').click();
  await wait(30);
  assert.equal(window.location.hash, "#/review");
  assert.equal(document.querySelector('.side a[aria-current="page"]').dataset.nav, "review");

  document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "b", ctrlKey: true, bubbles: true }));
  assert.equal(shell.dataset.navCollapsed, "true", "Ctrl+B did not collapse the primary sidebar");
});

test("navigation drawer is keyboard-safe on mobile and restores toggle focus", async () => {
  window.matchMedia = (query) => ({
    matches: query === "(max-width: 820px)",
    addEventListener() {},
    removeEventListener() {},
  });
  await boot();
  const toggle = document.querySelector("#nav-toggle");
  const nav = document.querySelector("#primary-navigation");
  const scrim = document.querySelector("#nav-scrim");
  const resizer = document.querySelector("#nav-resizer");
  assert.equal(toggle.getAttribute("aria-expanded"), "false");
  assert.equal(resizer.hidden, true);
  assert.equal(nav.hasAttribute("inert"), true);
  assert.equal(scrim.hidden, true);

  toggle.click();
  assert.equal(toggle.getAttribute("aria-expanded"), "true");
  assert.equal(scrim.hidden, false);
  document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  assert.equal(toggle.getAttribute("aria-expanded"), "false");
  assert.equal(document.activeElement, toggle);
  assert.equal(nav.hasAttribute("inert"), true);
});

test("appearance: auto resolves system theme and density persists through the Store", async () => {
  localStorage.removeItem("nayose.theme");
  localStorage.removeItem("nayose.density");
  window.matchMedia = () => ({ matches: true, addEventListener() {}, removeEventListener() {} });
  const { Store } = await import(join(ROOT, "js/store.js") + "?appearance=" + bootSeq++);
  const store = new Store();
  await boot({ store });
  assert.equal(store.theme, "auto");
  assert.equal(document.documentElement.dataset.theme, "dark");
  assert.equal(document.querySelector('meta[name="theme-color"]').content, "#0b1220");
  document.querySelector("#density").click();
  assert.equal(store.density, "compact");
  assert.equal(localStorage.getItem("nayose.density"), "compact");
  assert.equal(document.documentElement.dataset.density, "compact");
});

test("content panel sashes resize and persist while chrome stays independent", async () => {
  await boot();
  const searchSash = document.querySelector('[data-panel-resizer="search-workspace"]');
  assert.ok(searchSash, "search content sash is missing");
  assert.equal(searchSash.getAttribute("aria-valuenow"), "440");
  searchSash.dispatchEvent(new window.KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
  assert.equal(searchSash.getAttribute("aria-valuenow"), "456");
  assert.equal(JSON.parse(localStorage.getItem("nayose.layout.search-workspace")).width, 456);
  assert.equal(document.querySelector(".app-bar").querySelector("[data-panel-resizer]"), null, "top bar must not be resizable");
  assert.equal(document.querySelector("#status-strip").querySelector("[data-panel-resizer]"), null, "status bar must not be resizable");

  window.location.hash = "#/entities";
  await wait(100);
  const explorerSash = document.querySelector('[data-panel-resizer="entity-explorer"]');
  assert.ok(explorerSash, "entity explorer content sash is missing");
  explorerSash.dispatchEvent(new window.KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
  assert.equal(explorerSash.getAttribute("aria-valuenow"), "536");
  assert.equal(JSON.parse(localStorage.getItem("nayose.layout.entity-explorer")).width, 536);
});

test("search: typing renders live cards and selection loads detail in place", async () => {
  await boot();
  const input = document.querySelector("#q");
  input.value = "佐藤";
  input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  await wait(400);
  const hits = [...document.querySelectorAll("#results .hit")];
  assert.equal(hits.length, 1);
  assert.equal(hits[0].getAttribute("href"), "#/search?entity=N000000001");
  assert.equal(document.querySelector("#stat").textContent, "1 件");
  assert.match(hits[0].textContent, /類似検索（文字ベース） 91%/, "semantic similarity chip rendered when present");
  hits[0].dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, cancelable: true }));
  await wait(30);
  assert.equal(window.location.hash, "#/search?q=%E4%BD%90%E8%97%A4&entity=N000000001");
  assert.equal(document.querySelector("#search-detail").hidden, false);
  assert.equal(document.querySelector("#search-detail [data-selected-entity]").dataset.selectedEntity, "N000000001");
  assert.match(document.querySelector("#search-detail").textContent, /佐藤 太郎/);
  assert.ok(document.querySelector("#search-detail .search-workspace-detail-header"));
  assert.equal(document.querySelector("#search-detail .search-workspace-detail-members").open, false);
  assert.ok(document.querySelector("#q"), "search bar remains mounted while details load");
  input.value = "鈴木";
  input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  assert.equal(document.querySelector("#search-detail").hidden, true, "new queries clear the previous selection");
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

test("palette: dialog semantics, focus trap/restore, and action vocabulary", async () => {
  await boot();
  const cmd = document.querySelector("#cmd");
  const palette = document.querySelector("#palette");
  cmd.click();
  assert.equal(palette.getAttribute("role"), "dialog");
  assert.equal(palette.getAttribute("aria-modal"), "true");
  assert.equal(document.activeElement.id, "palette-input");
  document.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Tab", bubbles: true }));
  assert.equal(document.activeElement.id, "palette-input", "focus escaped the command dialog");
  document.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  assert.equal(palette.hidden, true);
  assert.equal(document.activeElement.id, "cmd", "focus was not restored to the opener");
  cmd.click();
  const input = document.querySelector("#palette-input");
  input.value = "density";
  input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  await wait(40);
  const compact = [...document.querySelectorAll("#palette-results li")].find(li => li.textContent.includes("density-compact"));
  assert.ok(compact, "density action was not discoverable in the palette");
  compact.click();
  assert.equal(document.documentElement.dataset.density, "compact");
  assert.equal(palette.hidden, true);
  assert.equal(document.activeElement.id, "cmd");
  cmd.click();
  input.value = "ナビゲーション";
  input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  await wait(40);
  const navigationAction = [...document.querySelectorAll("#palette-results li")].find(li => li.textContent.includes("ナビゲーション"));
  assert.ok(navigationAction, "navigation action was not discoverable in the palette");
  navigationAction.click();
  assert.equal(document.querySelector(".shell").dataset.navCollapsed, "true");
});

test("review: rows render, 既読 toggles run-scoped server state, 取り消し undoes", async () => {
  await boot();
  window.location.hash = "#/review";
  await wait(100);
  const rows = [...document.querySelectorAll("#view tbody tr")];
  assert.equal(rows.length, 2);
  assert.ok(document.querySelector("#review-record-help"));
  const sibling = rows[1];
  const siblingHtml = sibling.outerHTML;
  rows[0].querySelector("button.check").click();
  await wait(20);
  assert.match(rows[0].querySelector("button.check").textContent, /記録済|Recorded/);
  assert.strictEqual(document.querySelectorAll("#view tbody tr")[1], sibling, "toggle re-rendered a sibling row");
  assert.equal(sibling.outerHTML, siblingHtml, "toggle changed an unrelated row");
  await wait(600);
  assert.equal(mockPort.savedDecisions.length, 1, "checked decision was not persisted for the active run");
  document.querySelector("#undo").click();
  assert.match(document.querySelector("#view tbody button.check").textContent, /既読|Reviewed/);
  await wait(80);
  document.querySelector("#view tbody button.check").click();
  document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "z", metaKey: true, bubbles: true }));
  await wait(80);
  assert.match(document.querySelector("#view tbody button.check").textContent, /既読|Reviewed/, "Command-Z did not undo the review decision");
  await wait(600);
  assert.equal(mockPort.savedDecisions.length, 0, "undo restored the server decision state");
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
  try {
    await boot();
    window.location.hash = "#/review";
    await wait(100);
    const firstRow = document.querySelector("#view tbody tr");
    assert.ok(firstRow.classList.contains("checked"), "restored decision marked checked");
  } finally {
    mockPort.savedDecisions = [];
  }
});

test("run changes reload only the selected run and cannot persist stale decisions", async () => {
  const originalLoad = mockPort.loadDecisions;
  const loaded = [];
  mockPort.savedDecisions = [];
  mockPort.loadDecisions = (run) => {
    loaded.push(run);
    return Promise.resolve({ items: run === "runB" ? [{ left: "R0000000002", right: "R0000000003" }] : [], revision: 0 });
  };
  try {
    await boot({ runs: [{ id: "runA", current: true, fingerprint: "a" }, { id: "runB", current: false, fingerprint: "b" }] });
    window.location.hash = "#/review";
    await wait(100);
    document.querySelector("#view tbody button.check").click();
    document.querySelector("#run").value = "runB";
    document.querySelector("#run").dispatchEvent(new dom.window.Event("change", { bubbles: true }));
    await wait(120);
    assert.equal(mockPort._run, "runB");
    assert.deepEqual([...mockPort.savedDecisions], [], "old-run edits leaked into the new run");
    assert.ok(document.querySelectorAll("#view tbody tr")[1].classList.contains("checked"), "new-run decisions were not restored");
    assert.deepEqual(loaded.slice(-2), ["runA", "runB"]);
  } finally {
    mockPort.loadDecisions = originalLoad;
    mockPort.savedDecisions = [];
  }
});

test("decision conflicts are visible and can reload the server revision", async () => {
  mockPort.savedDecisions = [];
  mockPort.decisionRevision = 0;
  await boot();
  window.location.hash = "#/review";
  await wait(100);
  mockPort.decisionRevision = 1;
  document.querySelector("#view tbody button.check").click();
  await wait(700);
  assert.ok(document.querySelector("#decision-conflict"), "conflict notice was not rendered");
  document.querySelector("#decision-reload").click();
  await wait(100);
  assert.equal(document.querySelector("#decision-conflict"), null, "reload did not clear conflict");
  await wait(600);
  mockPort.decisionRevision = 0;
  mockPort.savedDecisions = [];
});

test("decision save failures remain visible and offer a retry", async () => {
  const originalSave = mockPort.saveDecisions;
  mockPort.saveDecisions = async () => { throw new Error("request_timeout"); };
  try {
    await boot();
    window.location.hash = "#/review";
    await wait(100);
    document.querySelector("#view tbody button.check").click();
    await wait(650);
    assert.ok(document.querySelector("#decision-save-error"), "save failure was hidden");
    assert.ok(document.querySelector("#decision-retry"), "save failure had no recovery action");
  } finally {
    mockPort.saveDecisions = originalSave;
  }
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
  assert.ok(cards[0].querySelector("progress.meter"), "accessible progress meter present");
});
test("MemoProxy: memoizes reads, clears on run switch, never caches mutations", async () => {
  const { MemoProxy } = await import(join(ROOT, "js/infra.js"));
  const calls = { entities: 0, recommend: 0, saveDecisions: 0, health: 0 };
  const inner = {
    run() {},
    entities: async () => { calls.entities++; return []; },
    entity: async () => ({}),
    health: async () => { calls.health++; return {}; },
    review: async () => ({ total: 0, rows: [], histogram: [], pair_auto_threshold: null }),
    metrics: async () => ({ kpis: [], extras: {} }),
    metricsFor: async () => ({ kpis: [], extras: {} }),
    confusion: async () => ({ pairs: {}, scenarios: [], decisions: {} }),
    compare: async () => [],
    recommend: async () => { calls.recommend++; return {}; },
    loadDecisions: async () => [],
    saveDecisions: async () => { calls.saveDecisions++; },
  };
  const proxy = new MemoProxy(inner);
  proxy.run("r1");
  await proxy.entities("x");
  await proxy.entities("x");
  assert.equal(calls.entities, 1, "same (run, op, args) resolved once");
  await proxy.entities("y");
  assert.equal(calls.entities, 2, "different args miss the cache");
  await proxy.recommend({});
  await proxy.recommend({});
  assert.equal(calls.recommend, 2, "mutations always go through");
  await proxy.saveDecisions([]);
  await proxy.saveDecisions([]);
  assert.equal(calls.saveDecisions, 2, "saves always go through");
  proxy.run("r2");
  await proxy.entities("x");
  assert.equal(calls.entities, 3, "run switch invalidates the cache");
  await proxy.health();
  await proxy.health();
  assert.equal(calls.health, 2, "health must not be cached during polling");
});

test("language toggle persists, translates navigation and keeps customer input", async () => {
  await boot();
  const field = document.querySelector("#q");
  field.value = "佐藤 太郎";
  field.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  document.querySelector("#language").click();
  await wait(80);
  assert.equal(document.documentElement.lang, "en");
  assert.equal(localStorage.getItem("nayose.locale"), "en");
  assert.equal(document.querySelector('[data-nav="entities"]').textContent, "Entity explorer");
  assert.equal(document.querySelector("#q").value, "佐藤 太郎");
  assert.match(document.querySelector("#view").textContent, /Search existing customers/);
});

test("entity explorer uses URL state, compact footprint, and progressive evidence", async () => {
  await boot();
  window.location.hash = "#/entities";
  await wait(120);
  assert.ok(document.querySelector("#entity-explorer-query"));
  assert.ok(document.querySelector("#entity-explorer-footprint"));
  assert.ok(document.querySelector("#entity-explorer-observed"));
  assert.equal(document.querySelector("#entity-explorer-observed").open, false);
  assert.equal(document.querySelector(".entity-explorer-map"), null);
  assert.match(document.querySelector("[data-footprint-kind='record']").textContent, /1/);
  assert.match(document.querySelector("#view").textContent, /佐藤 太郎/);
  document.querySelector("#entity-explorer-observed summary").click();
  assert.match(document.querySelector("#entity-explorer-observed").textContent, /Source record|元レコード/);
  assert.doesNotMatch(document.querySelector("#view").textContent, /reference_records/);
  document.querySelector("#entity-explorer-query").value = "鈴木";
  document.querySelector("#entity-explorer-form").dispatchEvent(new dom.window.Event("submit", { bubbles: true, cancelable: true }));
  await wait(120);
  assert.match(window.location.hash, /q=%E9%88%B4%E6%9C%A8/);
  assert.match(document.querySelector("#view").textContent, /鈴木 花子/);
});

test("entity members paginate, expose provenance and offer record comparison", async () => {
  await boot();
  window.location.hash = "#/entity/N000000001";
  await wait(100);
  assert.equal(document.querySelectorAll("[data-member]").length, 25);
  assert.match(document.querySelector("#member-count").textContent, /30/);
  assert.match(document.querySelector("#view").textContent, /paper_ocr/);
  document.querySelectorAll("[data-member]")[0].click();
  document.querySelectorAll("[data-member]")[1].click();
  assert.equal(document.querySelector("#member-compare").disabled, false, "member row selection did not enable compare");
  document.querySelector("#member-clear").click();
  document.querySelector("#member-next").click();
  await wait(100);
  assert.equal(document.querySelectorAll("[data-member]").length, 5);
  assert.match(window.location.hash, /page=2/);
  assert.match(document.querySelector("#view").textContent, /R25/);
  assert.doesNotMatch(document.querySelector("#view").textContent, /homophone_kanji/);
});

test("search workspace removes the separate match-input editor", async () => {
  await boot();
  assert.ok(document.querySelector("#q"));
  assert.equal(document.querySelector("#rt-form"), null);
  assert.equal(document.querySelector("#rt-name"), null);
  const bar = document.querySelector(".search-workspace-bar");
  assert.ok(bar);
  assert.equal(bar.classList.contains("is-compact"), false);
  Object.defineProperty(window, "scrollY", { configurable: true, value: 120 });
  window.dispatchEvent(new dom.window.Event("scroll"));
  assert.equal(bar.classList.contains("is-compact"), true);
  Object.defineProperty(window, "scrollY", { configurable: true, value: 0 });
  window.dispatchEvent(new dom.window.Event("scroll"));
  assert.equal(bar.classList.contains("is-compact"), false);
  assert.equal(document.querySelector("#search-detail").hidden, true);
});

test("language preference restores on boot and translates every route", async () => {
  localStorage.setItem("nayose.locale", "en");
  await boot();
  assert.equal(document.documentElement.lang, "en");
  for (const [route, expected] of [["review", /Record pairs awaiting review/], ["entities", /Entity footprint and provenance/], ["metrics", /Metrics/], ["confusion", /Pair classifier/], ["compare-runs", /Compare key metrics/]]) {
    window.location.hash = `#/${route}`;
    await wait(100);
    assert.match(document.querySelector("#view").textContent, expected);
  }
  document.querySelector("#language").click();
  await wait(50);
  assert.equal(document.documentElement.lang, "ja");
  assert.equal(localStorage.getItem("nayose.locale"), "ja");
});

test("member comparison selection survives pagination and language changes", async () => {
  await boot();
  window.location.hash = "#/entity/N000000001";
  await wait(100);
  document.querySelector('[data-select="R0"]').click();
  document.querySelector("#member-next").click();
  await wait(100);
  document.querySelector('[data-select="R25"]').click();
  document.querySelector("#language").click();
  await wait(100);
  assert.equal(document.querySelector("#member-compare").disabled, false);
  document.querySelector("#member-compare").click();
  await wait(100);
  assert.equal(window.location.hash, "#/compare/R0/R25");
});

test("matching state discards a late response after switching model runs", async () => {
  const { Store } = await import(join(ROOT, "js/store.js"));
  const store = new Store();
  store.setRun("old");
  store.matching.draft = { name_kanji: "佐藤" };
  let finish;
  const pending = store.matching.start({ startRecommendation: () => new Promise(resolve => { finish = resolve; }) });
  assert.equal(store.matching.submitting, true);
  store.setRun("new");
  finish({ id: "old-job", stage: "complete", result: MATCH_RESULT });
  await pending;
  assert.equal(store.matching.job, null);
  assert.deepEqual(store.matching.draft, {});
});

test("empty models state shows guidance instead of a broken UI", async () => {
  await boot({ runs: [] });
  assert.match(document.querySelector("#view").textContent, /run バンドルが見つかりません/);
});

test("review: band histogram + score slider + page size render and drive refresh", async () => {
  await boot();
  window.location.hash = "#/review";
  await wait(100);
  const slider = document.querySelector("#minp");
  assert.ok(slider, "score slider present");
  assert.ok(document.querySelector(".hist .hist-bar"), "score-band histogram rendered");
  assert.ok(document.querySelector(".hist-mark"), "pair_auto marker rendered");
  slider.value = "50";
  slider.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  await wait(150);
  assert.equal(slider.getAttribute("aria-valuetext"), "50%");
  const size = document.querySelector("#page-size");
  size.value = "25";
  size.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  await wait(150);
  assert.ok(document.querySelector("#view table"), "table re-rendered after page-size change");
});

test("search workspace keeps matching controls out of the search surface", async () => {
  await boot();
  assert.equal(document.querySelector("#rt-go"), null);
  assert.equal(document.querySelector("#rt-out"), null);
});

test("status strip renders health: run, fingerprint and engine state", async () => {
  await boot();
  const strip = document.querySelector("#status-strip");
  await wait(80);
  assert.ok(!strip.hidden, "strip visible after first health poll");
  assert.match(strip.textContent, /run_fake/);
  assert.match(strip.textContent, /fingerprint|一致|OK/);
  assert.match(strip.textContent, /Engine ready|準備済み/);
  assert.match(strip.textContent, /arm64/);
  assert.match(strip.textContent, /RAM available|空きRAM/);
  assert.match(strip.textContent, /Resource details|リソース詳細/);
  assert.match(strip.textContent, /config123456|設定ハッシュ/);
});

test("admin capability has no separate token and workspace opens immediately", async () => {
  localStorage.setItem("nayose.adminToken", "obsolete");
  await boot();
  await wait(100);
  assert.equal(localStorage.getItem("nayose.adminToken"), null, "obsolete admin credential was removed");
  window.location.hash = "#/admin";
  await wait(120);
  assert.ok(document.querySelector("#gen-profile"), "admin workspace rendered without a token");
});

test("admin is open by default: nav visible and workspace renders without any token", async () => {
  await boot();
  await wait(100);
  assert.ok(!document.querySelector("#nav-admin")?.hasAttribute("hidden"), "nav shown when server is admin-capable");
  window.location.hash = "#/admin";
  await wait(120);
  assert.ok(document.querySelector("#gen-profile"), "workspace rendered without a token (open-by-default)");
});

test("admin load failure shows recovery guidance without a credential prompt", async () => {
  const original = mockPort.adminState;
  mockPort.adminState = async () => { throw new Error("server_unavailable"); };
  try {
    await boot();
    window.location.hash = "#/admin";
    await wait(120);
    assert.match(document.querySelector("#view").textContent, /管理機能を利用できません|Admin unavailable/);
    assert.match(document.querySelector("#view").textContent, /API が起動|API is running/);
    assert.equal(document.querySelector("#adm-token"), null, "admin token prompt must not return");
  } finally {
    mockPort.adminState = original;
  }
});

test("admin workspace: open nav, presets, settings apply + audit rows render", async () => {
  await boot();
  await wait(80);
  assert.ok(!document.querySelector("#nav-admin")?.hasAttribute("hidden"), "admin nav visible when server confirms capability");
  window.location.hash = "#/admin";
  await wait(120);
  assert.ok(document.querySelector("#gen-profile"), "profile presets rendered");
  assert.ok(document.querySelector("#admin-configuration"), "canonical configuration read model rendered");
  assert.equal(document.querySelector("#admin-configuration .configuration-readonly").textContent, "読み取り専用");
  assert.ok(document.querySelector("#config-section-serving"), "serving config section rendered");
  const configuration = document.querySelector("#admin-configuration");
  assert.equal(configuration.open, false, "configuration details should be collapsed by default");
  configuration.querySelector("summary").click();
  assert.equal(configuration.open, true, "configuration details did not expand");
  assert.match(configuration.textContent, /config123/);
  assert.ok(document.querySelector("#set-exact"), "serving settings rendered");
  assert.equal(document.querySelector("#tr-max").value, "100000", "training defaults did not come from canonical config");
  assert.equal(document.querySelector("#tr-cap").value, "80", "candidate cap did not come from canonical config");
  assert.match(document.querySelector("#view").textContent, /監査ログ|Audit log/);
  await wait(80);
  assert.ok(document.querySelector("#dataset-explorer"), "dataset explorer did not render");
  assert.ok(document.querySelector("#dataset-explorer table"), "dataset rows did not render");
  const exact = document.querySelector("#set-exact");
  exact.value = "exact_only";
  exact.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  document.querySelector("#set-go").click();
  await wait(120);
  assert.equal(mockPort.exactMode, "exact_only", "settings applied through the port");
  assert.match(document.querySelector("#view").textContent, /設定を適用しました|Settings applied/);
  const confirm = document.querySelector("#dz-confirm");
  assert.ok(confirm, "danger zone confirm checkbox rendered");
  assert.equal(document.querySelector("#dz-prune").disabled, true, "destructive buttons locked until confirmed");
});

test("dataset viewer selects datasets and filters visible columns", async () => {
  const originalGenerations = mockPort.adminGenerations;
  const originalSummary = mockPort.adminDatasetSummary;
  const originalRows = mockPort.adminDatasetRows;
  const calls = [];
  mockPort.adminGenerations = () => Promise.resolve([
    { id: "gen_fake", label_en: "Demo · 2 entities · 3 observations", label_ja: "デモ · 2 エンティティ · 3 観測", description_en: "Demo dataset", description_ja: "デモデータ", current: true, created_at: "2026-01-01", fingerprint: "abc123", snapshot_date: "2025-12-31", query_date: "2026-08-25", counts: { parties: 2, observations: 3, queries: 1 } },
    { id: "gen_two", label_en: "Archive · 4 entities · 8 observations", label_ja: "保存 · 4 エンティティ · 8 観測", description_en: "Archived dataset", description_ja: "保存データ", current: false, created_at: "2025-01-01", fingerprint: "def456", snapshot_date: "2024-12-31", query_date: "2025-08-25", counts: { parties: 4, observations: 8, queries: 2 } },
  ]);
  mockPort.adminDatasetSummary = (id) => Promise.resolve({ id, current: id === "gen_fake", label_en: id, label_ja: id, description_en: "dataset", description_ja: "データ", created_at: "2026-01-01", fingerprint: "abc123", config: {}, counts: { parties: 2, observations: 3, queries: 1 }, tables: [{ id: "reference", label: "Customer observations", description: "observed", rows: 3, columns: ["record_id", "name_kanji", "phone"] }, { id: "queries", label: "Matching cases", rows: 1, columns: ["query_id", "name_kanji"] }, { id: "agencies", label: "Source offices", rows: 1, columns: ["agency_id"] }] });
  mockPort.adminDatasetRows = (id, table, query, offset, limit, filters, includeTotal, after) => {
    calls.push({ id, table, query, offset, limit, filters, after });
    const nextPage = Boolean(after);
    return Promise.resolve({ table, columns: table === "reference" ? ["record_id", "name_kanji", "phone"] : ["query_id", "name_kanji"], rows: [{ record_id: nextPage ? "R1" : "R0", name_kanji: "佐藤 太郎", phone: "090-1" }], total: -1, total_exact: false, has_more: !nextPage, next_cursor: nextPage ? null : "R0", offset, limit });
  };
  try {
    await boot();
    window.location.hash = "#/admin";
    await wait(180);
    assert.equal(document.querySelectorAll("[data-dataset-select]").length, 2);
    const second = document.querySelector('[data-dataset-select="gen_two"]');
    second.click();
    assert.equal(document.querySelectorAll("[data-dataset-tab]").length, 2);
    document.querySelector('[data-dataset-tab="gen_two"]').click();
    await wait(80);
    assert.equal(calls.at(-1).id, "gen_two");
    document.querySelector("#dataset-next").click();
    await wait(80);
    assert.equal(calls.at(-1).after, "R0", "next page did not use the keyset cursor");
    document.querySelector("#dataset-prev").click();
    await wait(80);
    assert.equal(calls.at(-1).after, undefined, "previous page did not restore the cursor history");
    assert.equal(document.querySelector('[data-column-lock="name_kanji"]'), null, "dataset grid must not expose a non-functional sticky-column control");
    assert.equal(document.querySelector('[data-column="name_kanji"].dataset-grid-column-locked'), null, "table cells must not use sticky-column classes");
    const filter = document.querySelector('[data-column-filter="name_kanji"]');
    filter.value = "佐藤";
    document.querySelector("#dataset-filter-apply").click();
    await wait(80);
    assert.equal(calls.at(-1).filters.name_kanji, "佐藤");
  } finally {
    mockPort.adminGenerations = originalGenerations;
    mockPort.adminDatasetSummary = originalSummary;
    mockPort.adminDatasetRows = originalRows;
  }
});

test("admin generator: all sabotage controls are visible, preview is tunable, and generate uses the previewed controls", async () => {
  await boot();
  window.location.hash = "#/admin";
  await wait(180);
  assert.equal(document.querySelectorAll("#sabotage-table tbody tr").length, EVENT_IDS.length, "not every registered sabotage event is visible");
  assert.equal(document.querySelectorAll("#preview-events tbody tr").length, EVENT_IDS.length, "preview omitted a sabotage event");
  assert.ok(document.querySelector("#gen-preview .kpi"), "generation preview did not render summary counts");
  assert.ok(document.querySelector("#gen-sample"), "live data example did not render");
  assert.equal(document.querySelectorAll("#gen-sample tbody tr").length, 9, "live example omitted a field");
  const panelToggle = document.querySelector("#gen-preview-toggle");
  const panelBody = document.querySelector("#gen-preview-body");
  panelToggle.click();
  assert.equal(panelBody.hidden, true, "preview panel did not collapse");
  assert.equal(panelToggle.getAttribute("aria-expanded"), "false");
  panelToggle.click();
  const resizer = document.querySelector("#gen-preview-resizer");
  resizer.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }));
  assert.equal(JSON.parse(localStorage.getItem("nayose.layout.admin-workbench")).previewWidth, 436, "keyboard resize was not persisted");
  document.querySelector("#gen-sample-quality").value = "catastrophic";
  document.querySelector("#gen-sample-quality").dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  await wait(30);
  assert.equal(document.querySelector("#gen-sample-quality").value, "catastrophic");
  const controls = document.querySelector("#sabotage-controls");
  const event = document.querySelector('[data-event-rate="paper_ocr"]');
  event.value = "72";
  event.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  await wait(300);
  assert.equal(mockPort.lastGenerationPreview.event_rates.paper_ocr, 0.72);
  assert.equal(document.querySelector('[data-event-value="paper_ocr"]').value, "72%");
  document.querySelector("#gen-reset").click();
  await wait(40);
  assert.equal(document.querySelector('[data-event-rate="paper_ocr"]').value, "5", "reset did not restore the default event rate");
  assert.equal(mockPort.lastGenerationPreview.event_rates.paper_ocr, 0.05, "reset did not preview the default controls");
  assert.equal(document.querySelector("#sabotage-controls"), controls, "reset rebuilt the whole admin page");
  event.value = "72";
  event.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  await wait(300);
  assert.equal(mockPort.lastGenerationPreview.event_rates.paper_ocr, 0.72);
  assert.equal(document.querySelector("#sabotage-controls"), controls, "parameter tuning rebuilt the whole admin page");
  const profile = document.querySelector("#gen-profile");
  profile.value = "mac";
  profile.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  await wait(300);
  assert.equal(document.querySelector("#gen-n").value, "0", "profile change did not restore profile-sized generation");
  assert.equal(document.querySelector("#gen-mean").value, "2.7");
  document.querySelector("#gen-go").click();
  await wait(80);
  assert.equal(mockPort.lastAdminStart.params.event_rates.paper_ocr, 0.72, "generate ignored tuned controls");
});

test("preview loading keeps the last result blurred and controls mounted", async () => {
  await boot();
  window.location.hash = "#/admin";
  await wait(260);
  const original = mockPort.adminGenerationPreview;
  let resolvePreview;
  mockPort.adminGenerationPreview = controls => new Promise(resolve => {
    mockPort.lastGenerationPreview = controls;
    resolvePreview = () => resolve(mockGenerationPreview(controls));
  });
  try {
    const controls = document.querySelector("#sabotage-controls");
    const event = document.querySelector('[data-event-rate="paper_ocr"]');
    event.value = "68";
    event.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    await wait(260);
    assert.ok(document.querySelector(".preview-loading.is-loading"), "preview did not enter an isolated loading state");
    assert.ok(document.querySelector("#gen-preview .kpi"), "last preview was removed while loading");
    assert.equal(document.querySelector("#sabotage-controls"), controls, "controls were rerendered during preview loading");
    assert.equal(document.querySelector("#gen-preview").getAttribute("aria-busy"), "true");
    resolvePreview();
    await wait(40);
    assert.equal(document.querySelector(".preview-loading.is-loading"), null, "preview did not leave loading state");
  } finally {
    mockPort.adminGenerationPreview = original;
  }
});

test("data entry wizard: match check -> register -> undo", async () => {
  await boot();
  window.location.hash = "#/data-entry";
  await wait(120);
  const name = document.querySelector('[data-field="name_kanji"]');
  assert.ok(name, "entry form rendered");
  assert.equal(document.querySelector('[aria-current="step"]')?.dataset.entryStep, "1");
  name.value = "手入力 太郎";
  name.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  document.querySelector("#de-check").click();
  assert.equal(document.querySelector('[aria-current="step"]')?.dataset.entryStep, "2");
  await wait(150);
  assert.equal(document.querySelector('[aria-current="step"]')?.dataset.entryStep, "3");
  assert.equal(document.querySelector("#de-register").disabled, false, "register enabled after match check");
  document.querySelector("#de-register").click();
  await wait(100);
  assert.match(document.querySelector("#view").textContent, /M00/);
  const undo = document.querySelector(".undo-m");
  assert.ok(undo, "undo button rendered per manual row");
  undo.click();
  await wait(80);
  assert.match(document.querySelector("#view").textContent, /0/);
});

test("F knobs: entity list size+sort, metrics unit toggle", async () => {
  await boot();
  window.location.hash = "#/entities";
  await wait(120);
  assert.ok(document.querySelector("#entity-explorer-size"), "page-size knob present");
  assert.ok(document.querySelector("#entity-explorer-sort"), "sort knob present");
  const size = document.querySelector("#entity-explorer-size");
  size.value = "100";
  size.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  await wait(120);
  const sort = document.querySelector("#entity-explorer-sort");
  sort.value = "name";
  sort.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  await wait(120);
  window.location.hash = "#/metrics";
  await wait(100);
  const unit = document.querySelector("#met-unit");
  assert.ok(unit, "unit toggle present");
  unit.value = "ratio";
  unit.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  await wait(50);
  assert.match(document.querySelector("#view").textContent, /0\.9900/);
});

test("ApiAdapter is the sole transport and attaches the bearer token", async () => {
  const { ApiAdapter } = await import(join(ROOT, "js/infra.js") + "?a=" + Date.now());
  const realFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, opts) => {
    calls.push({ url, headers: opts?.headers });
    return { ok: true, json: async () => String(url).includes("/api/entities") ? { rows: [], semantic: false } : [] };
  };
  try {
    sessionStorage.removeItem("nayose.token");
    localStorage.removeItem("nayose.token");
    await new ApiAdapter().runs();
    const plain = calls[0].headers instanceof Headers ? calls[0].headers.get("Authorization") : calls[0].headers?.Authorization;
    assert.equal(plain, null, "no token => no auth header");
    sessionStorage.setItem("nayose.token", "sekret");
    await new ApiAdapter().entities("x");
    const authed = calls[1].headers instanceof Headers ? calls[1].headers.get("Authorization") : calls[1].headers?.Authorization;
    assert.equal(authed, "Bearer sekret", "token attached by the transport, not by views");
  } finally {
    globalThis.fetch = realFetch;
    sessionStorage.removeItem("nayose.token");
    localStorage.removeItem("nayose.token");
  }
});

test("ApiAdapter rejects malformed admin configuration clearly", async () => {
  const { ApiAdapter } = await import(join(ROOT, "js/infra.js") + "?admin-config-schema=" + Date.now());
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ editable: true, sections: {} }) });
  try {
    await assert.rejects(() => new ApiAdapter().adminConfig(), /admin_config_schema_mismatch/);
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("ApiAdapter rejects a legacy generation preview contract clearly", async () => {
  const { ApiAdapter } = await import(join(ROOT, "js/infra.js") + "?legacy=" + Date.now());
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ counts: {} }) });
  try {
    await assert.rejects(() => new ApiAdapter().adminGenerationPreview(GENERATION_CATALOG.defaults), /preview_schema_mismatch/);
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("ApiAdapter rejects malformed Explorer page and graph payloads", async () => {
  const { ApiAdapter } = await import(join(ROOT, "js/infra.js") + "?explorer-schema=" + Date.now());
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url) => ({ ok: true, json: async () => String(url).includes("/graph") ? { entity_id: "N1", nodes: [], edges: [] } : { rows: [], total: 0, offset: 0, limit: 25, query: "", sort: "records", filters: {}, query_plan: {}, search_mode: "exact", total_is_exact: true, search_trace: {} } });
  try {
    await assert.rejects(() => new ApiAdapter().entityGraph("N1"), /entity_graph_schema_mismatch/);
  } finally {
    globalThis.fetch = realFetch;
  }

  globalThis.fetch = async () => ({ ok: true, json: async () => ({ rows: [], total: 0, offset: 0, limit: 25, query: "", sort: "invalid", filters: {}, query_plan: {}, search_mode: "exact", total_is_exact: true, search_trace: {} }) });
  try {
    await assert.rejects(() => new ApiAdapter().entityExplorer("", 0, 25, "records"), /entity_explorer_schema_mismatch/);
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("ApiAdapter rejects malformed dataset pages", async () => {
  const { ApiAdapter } = await import(join(ROOT, "js/infra.js") + "?dataset-schema=" + Date.now());
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ table: "reference", columns: [], rows: [], total: 0, offset: 0, limit: 100, next_cursor: 42 }) });
  try {
    await assert.rejects(() => new ApiAdapter().adminDatasetRows("gen", "reference", "", 0, 100), /dataset_rows_schema_mismatch/);
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("search suggestion (もしかして) applies the corrected term", async () => {
  await boot();
  const input = document.querySelector("#q");
  input.value = "zzz";
  input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  await wait(400);
  const link = document.querySelector('#search-correction [data-suggest]');
  assert.ok(document.querySelector(".search-suggestions"), "live suggestion panel rendered");
  assert.ok(document.querySelector(".search-suggestions-group"), "nearest group rendered");
  assert.ok(link, "suggestion link rendered directly below the search bar");
  assert.equal(document.querySelector("#results [data-suggest]"), null, "correction is not buried in results");
  assert.equal(link.textContent, "佐藤 太郎");
  link.click();
  await wait(400);
  assert.equal(document.querySelector("#q").value, "佐藤 太郎", "suggestion fills the query");
});

test("search filters use compact accessible toggles", async () => {
  await boot();
  const input = document.querySelector("#q");
  input.value = "佐藤";
  input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  await wait(400);
  const people = document.querySelector('[data-filter="person"]');
  assert.equal(people.getAttribute("aria-pressed"), "false");
  people.click();
  assert.equal(document.querySelector('[data-filter="person"]').getAttribute("aria-pressed"), "true");
  assert.ok(document.querySelector(".search-workspace-filter-label"));
  assert.ok(document.querySelector(".search-workspace-bar .search-workspace-filters"));
  assert.equal(document.querySelector(".search-workspace-results .search-workspace-filters"), null);
});

test("admin generate renders a live progress meter + ETA from job progress", async () => {
  const original = mockPort.adminStart;
  mockPort.adminStart = async () => ({ id: "gen", run_id: "admin", stage: "queued", elapsed_ms: 0, timeline: [{ stage: "queued", elapsed_ms: 0 }], progress: null, error: null, result: null });
  try {
    await boot();
    window.location.hash = "#/admin";
    await wait(120);
    mockPort.adminStep = 0;
    document.querySelector("#gen-go").click();
    await wait(200); // pending tick (pct 42, ETA 1:30) is rendered during polling
    const out = document.querySelector("#gen-job");
    assert.ok(out.querySelector(".meter"), "meter rendered during progress");
    assert.equal(out.querySelector(".meter").getAttribute("value"), "42", "meter reflects streamed pct");
    assert.match(out.textContent, /観測・汚染生成 42%|1:30/);
    await wait(1800); // next poll resolves complete -> status preserved after paint
    assert.match(document.querySelector("#gen-job").textContent, /完了|jobDone/i);
  } finally {
    mockPort.adminStart = original;
  }
});
