// Real-browser E2E (Playwright): starts the actual FastAPI server against a
// REAL bundle, drives Chromium, and asserts end-to-end behavior INCLUDING
// server-side decision persistence. Opt-in: NAYOSE_E2E=1 (a browser + a
// trained bundle are required).
//
// Authoring rules (enforced by tests/test_arch_contract.py):
//  - journeys get their page from runJourney() ONLY — every journey runs in a
//    fresh context (no state can leak between journeys)
//  - navigation/waits go through navigateFresh/waitVisible/waitBodyText/waitCount
//    (raw waitForFunction/reload/waitForSelector are the bugs that bit us)
//  - selectors use ids and semantic data-* hooks ([data-entity], [data-member]),
//    never structural tag paths scoped to the view container (that was the
//    original breakage: a tag-sibling selector matched the search card's own
//    heading instead of the entity page)
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import {
  BASE,
  cleanupFixture,
  ENABLED,
  FIXTURE,
  launchBrowser,
  navigateFresh,
  runJourney,
  searchSeed,
  startServer,
  waitBodyText,
  waitCount,
  waitHash,
  waitLang,
  waitManualCount,
  waitVisible,
} from "./e2e-util.mjs";

let server;
let browser;

before(async () => {
  if (!ENABLED) return;
  server = await startServer(FIXTURE.models, FIXTURE.data);
  browser = await launchBrowser();
});

after(async () => {
  if (browser) await browser.close();
  if (server) server.kill();
  cleanupFixture();
});

test("e2e: search -> entity -> decisions persisted server-side", { skip: !ENABLED }, async () => {
  await runJourney(browser, "search-entity-decisions", async (page) => {
    await navigateFresh(page);
    const term = await searchSeed(page);
    await page.fill("#q", term);
    await waitCount(page, "#results .hit", 1);
    const href = await page.getAttribute("#results .hit", "href");
    assert.ok(href && href.startsWith("#/entity/"), "result links to entity route");
    await page.click("#results .hit");
    await waitVisible(page, "#view [data-entity]");
    const escaped = term.replace(/[.*+?^${}()|[\]\\\\]/g, "\\$&");
    assert.match(await page.textContent("[data-entity] h2"), new RegExp(escaped));

    // review workflow: check an edge -> server-side persistence
    await navigateFresh(page, "#/review");
    await waitVisible(page, "[data-l] button.check");
    await page.locator("[data-l] button.check").first().click();
    await page.waitForTimeout(900); // debounce (500ms) + round trip
    const runId = await page.evaluate(() => document.querySelector("#run").value);
    const res = await fetch(`${BASE}/api/decisions?run_id=${encodeURIComponent(runId)}`);
    const body = await res.json();
    assert.ok(body.items.length >= 1, "decision persisted server-side");

    // cross-run diff renders
    await navigateFresh(page, "#/compare-runs");
    await waitVisible(page, "#go");
    await page.click("#go");
    await waitCount(page, "#out table tbody tr", 1);
    assert.match(await page.textContent("#out table"), /candidate recall/);
  });
});

test("e2e: palette keyboard flow navigates to entity", { skip: !ENABLED }, async () => {
  await runJourney(browser, "palette-keyboard", async (page) => {
    await navigateFresh(page, "#/search");
    await page.keyboard.press("Meta+k");
    await waitVisible(page, "#palette-input");
    await page.type("#palette-input", await searchSeed(page));
    // wait for an ACTUAL entity item — "items > 5" is trivially true (6 nav
    // entries render instantly), an assertion on the wrong artifact
    await waitBodyText(page, "N0", 10000);
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
    await waitHash(page, "#/entity/");
  });
});

test("e2e: review decision survives a page reload", { skip: !ENABLED }, async () => {
  await runJourney(browser, "review-reload", async (page) => {
    await navigateFresh(page, "#/review");
    // pick the first UNCHECKED pair (behavior-based): order-independent — runs
    // before or after the search journey, whichever pair it happens to mark.
    const unchecked = page
      .locator("tr[data-l]")
      .filter({ has: page.locator('button.check[aria-pressed="false"]') })
      .first();
    await unchecked.waitFor({ state: "visible" });
    const pairKey = await unchecked.getAttribute("data-l");
    await unchecked.locator("button.check").click();
    await page.waitForTimeout(900); // debounce (500ms) + round trip
    await navigateFresh(page, "#/review"); // true reload: decision must survive
    await waitVisible(page, `tr[data-l="${pairKey}"]`);
    const row = page.locator(`tr[data-l="${pairKey}"]`);
    assert.ok(await row.evaluate(el => el.classList.contains("checked")), "decision restored after reload");
    assert.equal(await row.locator("button.check").getAttribute("aria-pressed"), "true");
  });
});

test("e2e: language preference persists across reload, customer data untouched", { skip: !ENABLED }, async () => {
  await runJourney(browser, "language-persistence", async (page) => {
    await navigateFresh(page);
    // capture a customer value BEFORE switching, prove it is not translated
    const term = await searchSeed(page);
    await page.fill("#q", term);
    await waitCount(page, "#results .hit", 1);
    const kanjiBefore = await page.textContent("#results .hit h3");
    await page.click("#language");
    await waitLang(page, "en");
    assert.equal(await page.$$eval('[data-nav="entities"]', els => els[0].textContent), "Entities");
    await navigateFresh(page); // true reload: locale must survive
    assert.equal(await page.evaluate(() => document.documentElement.lang), "en", "locale restored from storage");
    await page.fill("#q", term);
    await waitCount(page, "#results .hit", 1);
    const kanjiAfter = await page.textContent("#results .hit h3");
    assert.equal(kanjiAfter, kanjiBefore, "customer data must not be translated");
    assert.match(await page.textContent("main"), /Find matches/);
  });
});

test("e2e: live match shows real stages, thresholds, evidence and backend (engine)", { skip: !ENABLED }, async () => {
  await runJourney(browser, "match-evidence", async (page) => {
    await navigateFresh(page, "#/search");
    await page.click("#rt-sample");
    // sample row is bundle-agnostic: every fixture renders a source record
    await waitBodyText(page, "入力元レコード", 10000);
    await page.click("#rt-go");
    await waitBodyText(page, "照合完了", 600000); // cold engine load can take minutes
    const out = await page.textContent("#rt-out");
    assert.match(out, /しきい値/, "thresholds disclosed");
    assert.match(out, /取得レコード/, "retrieval counts disclosed");
    assert.match(out, /候補と照合根拠/, "candidate evidence section present");
    assert.match(out, /検索方式/, "retrieval backend disclosed");
  });
});

test("e2e: entity member expansion + select two -> compare (real server)", { skip: !ENABLED }, async () => {
  await runJourney(browser, "entity-compare", async (page) => {
    await navigateFresh(page);
    const rows = await page.evaluate(async () => {
      const res = await fetch("/api/entities?q=&run_id=&limit=10");
      return (await res.json()).rows;
    });
    const big = rows.sort((a, b) => (b.record_count ?? 0) - (a.record_count ?? 0))[0];
    assert.ok(big && (big.record_count ?? 0) >= 2, "need an entity with >=2 members");
    await navigateFresh(page, `#/entity/${encodeURIComponent(big.nayose_id)}?page=1&size=25`);
    await waitVisible(page, "#view [data-entity]");
    // expand the first member's full record (the details row follows the
    // [data-member] row as an adjacent sibling)
    await page.locator("[data-member] + tr summary").first().click();
    await waitBodyText(page, "比較結果");
    // select the first two members -> compare link
    await page.locator("[data-select]").nth(0).click();
    await page.locator("[data-select]").nth(1).click();
    assert.equal(await page.locator("#member-compare").isEnabled(), true);
    await page.click("#member-compare");
    await waitVisible(page, ".diff");
    // the diffHelp line is a sibling of .diff — assert over the whole view
    assert.match(await page.textContent("#view"), /差分行|highlighted/);
  });
});

test("e2e: admin workspace — open access, presets, settings apply + audit render, danger locked", { skip: !ENABLED }, async () => {
  await runJourney(browser, "admin-workspace", async (page) => {
    await page.goto(BASE, { waitUntil: "commit" });
    await navigateFresh(page, "#/admin");
    await waitVisible(page, "#gen-profile");
    await waitVisible(page, "#gen-go");
    await waitVisible(page, "#dataset-explorer");
    await waitVisible(page, "#dataset-explorer table");
    assert.ok(await page.evaluate(() => !document.querySelector("#nav-admin")?.hasAttribute("hidden")), "admin nav revealed when capable");
    assert.ok(await page.locator("#gen-go").textContent().then(text => /データを生成|Generate data/.test(text ?? "")), "generation action is not discoverable");
    await page.selectOption("#dataset-table", "queries");
    await waitBodyText(page, "入力クエリ", 5000);
    await page.locator("#runtime-controls summary").click();
    // serving settings: change exact_mode + apply (audited)
    await page.selectOption("#set-exact", "exact_only");
    await page.click("#set-go");
    await waitBodyText(page, "設定を適用しました", 5000);
    // danger zone: locked until confirm
    assert.equal(await page.locator("#dz-prune").isDisabled(), true);
    await page.check("#dz-confirm");
    assert.equal(await page.locator("#dz-prune").isDisabled(), false);
    // audit rows visible
    assert.match(await page.textContent("#view"), /監査ログ/);
    // restore hybrid for later journeys
    await page.selectOption("#set-exact", "hybrid");
    await page.click("#set-go");
    await waitBodyText(page, "設定を適用しました", 5000);
  });
});

test("e2e: data entry wizard — match check, register, undo (manual corpus, isolated fixture)", { skip: !ENABLED }, async () => {
  await runJourney(browser, "data-entry-wizard", async (page) => {
    await page.goto(BASE, { waitUntil: "commit" });
    await navigateFresh(page, "#/data-entry");
    await waitVisible(page, '[data-field="name_kanji"]');
    await page.fill('[data-field="name_kanji"]', "E2E 登録 太郎");
    await page.fill('[data-field="phone"]', "090-1111-2222");
    await page.click("#de-check");
    try {
      await waitVisible(page, "#de-register:not([disabled])", 25000);
    } catch (err) {
      console.error("DATA-ENTRY state:", await page.evaluate(() => ({
        status: document.querySelector("#de-status")?.textContent ?? "",
        match: document.querySelector("#de-match")?.textContent ?? "",
        notice: document.querySelector(".notice")?.textContent ?? "",
      })));
      throw err;
    }
    await page.click("#de-register");
    await waitManualCount(page, 1); // register lands exactly one manual row
    await waitVisible(page, ".undo-m");
    await page.click(".undo-m");
    await waitManualCount(page, 0); // undo removed it
  });
});

test("e2e: F knobs — entity page size + sort, metrics unit toggle", { skip: !ENABLED }, async () => {
  await runJourney(browser, "display-knobs", async (page) => {
    await navigateFresh(page, "#/entities");
    await waitVisible(page, "#ent-size");
    await page.selectOption("#ent-size", "100");
    await waitVisible(page, "#ent-sort");
    await page.selectOption("#ent-sort", "name");
    await waitVisible(page, "#view .card a");
    await navigateFresh(page, "#/metrics");
    await waitVisible(page, "#met-unit");
    await page.selectOption("#met-unit", "ratio");
    await waitBodyText(page, "0.99");
  });
});

test("e2e: mobile viewport + reduced motion — no page-level overflow, chrome usable", { skip: !ENABLED }, async () => {
  await runJourney(
    browser,
    "mobile-reduced-motion",
    async (page) => {
      await navigateFresh(page);
      const metrics = await page.evaluate(() => ({
        overflow: document.documentElement.scrollWidth - window.innerWidth,
        navScrollable: getComputedStyle(document.querySelector(".side")).overflowX,
        langBtn: !!document.querySelector("#language"),
      }));
      assert.ok(metrics.overflow <= 1, `page overflows horizontally by ${metrics.overflow}px`);
      assert.match(metrics.navScrollable, /auto|scroll/, "side nav scrolls horizontally on mobile");
      assert.ok(metrics.langBtn, "language toggle present on mobile");
      await page.click("#language");
      await waitLang(page, "en");
    },
    { viewport: { width: 390, height: 844 }, reducedMotion: "reduce" },
  );
});