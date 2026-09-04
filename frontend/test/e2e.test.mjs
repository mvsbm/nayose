// Real-browser E2E (Playwright): starts the actual FastAPI server against a
// REAL bundle, drives Chromium, and asserts end-to-end behavior INCLUDING
// server-side decision persistence. Opt-in: NAYOSE_E2E=1 (a browser + a
// trained bundle are required). Hermetic-ish: fixed port, cleanup on exit.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const ROOT = dirname(dirname(dirname(fileURLToPath(import.meta.url)))); // repo root
const PORT = Number(process.env.NAYOSE_E2E_PORT ?? 8777);
const MODELS = process.env.NAYOSE_E2E_MODELS;
const DATA = process.env.NAYOSE_E2E_DATA;
const enabled = process.env.NAYOSE_E2E === "1" && MODELS && DATA;
const BASE = `http://127.0.0.1:${PORT}`;

let server;
let browser;
let page;

before(async () => {
  if (!enabled) return;
  server = spawn(join(ROOT, ".venv", "bin", "python"), ["-m", "api.main"], {
    cwd: ROOT,
    env: { ...process.env, NAYOSE_MODELS_DIR: MODELS, NAYOSE_DATA_DIR: DATA, NAYOSE_DASH_PORT: String(PORT) },
    stdio: "ignore",
  });
  // wait for healthy server
  for (let i = 0; i < 60; i++) {
    try {
      const res = await fetch(`${BASE}/api/runs`);
      if (res.ok) break;
    } catch {}
    await new Promise((r) => setTimeout(r, 250));
  }
  browser = await chromium.launch();
  page = await browser.newPage();
});

after(async () => {
  if (browser) await browser.close();
  if (server) server.kill();
});

test("e2e: search -> entity -> decisions persisted server-side", { skip: !enabled }, async () => {
  await page.goto(BASE);
  await page.waitForSelector("#q");
  await page.fill("#q", "佐藤");
  await page.waitForFunction(() => document.querySelectorAll("#results .hit").length > 0);
  const href = await page.getAttribute("#results .hit", "href");
  assert.ok(href && href.startsWith("#/entity/"), "result links to entity route");
  await page.click(`#results .hit`);
  await page.waitForSelector("#view h2");
  assert.match(await page.textContent("#view h2"), /佐藤/);

  // review workflow: check an edge -> server-side persistence
  await page.goto(BASE + "#/review");
  await page.waitForSelector("#view tbody tr button.check");
  await page.click("#view tbody tr button.check");
  await page.waitForTimeout(900); // debounce (500ms) + round trip

  const runId = await page.evaluate(() => document.querySelector("#run").value);
  const res = await fetch(`${BASE}/api/decisions?run_id=${encodeURIComponent(runId)}`);
  const body = await res.json();
  assert.ok(body.items.length >= 1, "decision persisted server-side");

  // cross-run diff renders
  await page.goto(BASE + "#/compare-runs");
  await page.waitForSelector("#go");
  await page.click("#go");
  await page.waitForFunction(() => document.querySelectorAll("#out table tbody tr").length > 0);
  assert.match(await page.textContent("#out table"), /candidate recall/);
});

test("e2e: palette keyboard flow navigates to entity", { skip: !enabled }, async () => {
  await page.goto(BASE + "#/search");
  await page.keyboard.press("Meta+k");
  await page.waitForSelector("#palette-input");
  await page.type("#palette-input", "佐藤");
  await page.waitForFunction(() => document.querySelectorAll("#palette-results li").length > 5);
  let hit = false;
  for (let i = 0; i < 10; i++) {
    await page.keyboard.press("ArrowDown");
    hit = await page.evaluate(() =>
      document.querySelector('#palette-results li[aria-selected="true"]')?.textContent?.includes("N0") ?? false,
    );
    if (hit) break;
  }
  assert.ok(hit, "keyboard reached an entity item");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => location.hash.startsWith("#/entity/"));
});