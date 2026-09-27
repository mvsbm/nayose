// Real-browser E2E (Playwright): starts the actual FastAPI server against a
// REAL bundle, drives Chromium, and asserts end-to-end behavior INCLUDING
// server-side decision persistence. Opt-in: NAYOSE_E2E=1 (a browser + a
// trained bundle are required).
//
// Authoring rules (enforced by tests/test_architecture_contract.py):
//  - journeys get their page from runJourney() ONLY — every journey runs in a
//    fresh context (no state can leak between journeys)
//  - navigation/waits go through navigateFresh/waitVisible/waitBodyText/waitCount
//    (low-level browser waits and document reloads are kept in the harness)
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
  waitAttribute,
  waitCount,
  waitComputedStyle,
  waitDecision,
  waitDecisionCount,
  waitHash,
  waitInputValue,
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
    assert.ok(href && href.startsWith("#/search?entity="), "result opens the search detail workspace");
    await page.click("#results .hit");
    await waitVisible(page, "#search-detail [data-selected-entity]");
    const selectedHeading = await page.textContent("#search-detail [data-selected-entity] h2");
    const compact = value => String(value ?? "").replace(/\s+/g, "");
    assert.ok(compact(selectedHeading).includes(compact(term)), "selected entity preserves the searched name");

    // review workflow: check an edge -> server-side persistence
    await navigateFresh(page, "#/review");
    await waitVisible(page, "[data-l] button.button-check");
    await page.locator("[data-l] button.button-check").first().click();
    const runId = await page.evaluate(() => document.querySelector("#run").value);
    await waitDecisionCount(page, runId, 1, 10000);
    const body = await page.evaluate(async id => {
      const response = await fetch(`/api/decisions?run_id=${encodeURIComponent(id)}`);
      return response.json();
    }, runId);
    assert.ok(body.items.length >= 1, "decision persisted server-side");

    // cross-run diff renders
    await waitDecisionCount(page, runId, 1, 10000);
    await navigateFresh(page, "#/compare-runs");
    await waitVisible(page, "#go");
    await page.click("#go");
    await waitCount(page, "#out table tbody tr", 1);
    assert.match(await page.textContent("#out table"), /candidate recall/);
  });
});

test("e2e: stale entity detail response cannot overwrite the latest selection", { skip: !ENABLED }, async () => {
  await runJourney(browser, "search-detail-race", async (page) => {
    await navigateFresh(page, "#/search");
    await page.fill("#q", await searchSeed(page));
    await waitCount(page, "#results .hit", 2);
    const ids = await page.$$eval("#results .hit", links =>
      links.slice(0, 2).map(link => link.getAttribute("data-entity-select")),
    );
    assert.ok(ids[0] && ids[1] && ids[0] !== ids[1], "need two distinct entity results");
    await page.route("**/api/entity/**", async route => {
      const id = new URL(route.request().url()).pathname.split("/").pop();
      if (id === ids[0]) await new Promise(resolve => setTimeout(resolve, 500));
      await route.continue();
    });
    await page.locator("#results .hit").nth(0).click();
    await page.locator("#results .hit").nth(1).click();
    await waitVisible(page, `#search-detail [data-selected-entity="${ids[1]}"]`);
    assert.equal(
      await page.locator("#search-detail [data-selected-entity]").getAttribute("data-selected-entity"),
      ids[1],
      "late response for the first selection replaced the latest detail",
    );
  });
});

test("e2e: stale search response cannot replace a newer query", { skip: !ENABLED }, async () => {
  await runJourney(browser, "search-query-race", async (page) => {
    await navigateFresh(page, "#/search");
    const terms = await page.evaluate(async () => {
      const response = await fetch("/api/entities?q=&run_id=&limit=10");
      const rows = (await response.json()).rows;
      return rows.slice(0, 2).map(row => String(row.name_kanji ?? "").slice(0, 2));
    });
    assert.ok(terms[0] && terms[1] && terms[0] !== terms[1], "need two distinct search terms");
    await page.route("**/api/entities?*", async route => {
      const query = new URL(route.request().url()).searchParams.get("q");
      if (query === terms[0]) await new Promise(resolve => setTimeout(resolve, 500));
      await route.continue();
    });
    const firstRequest = page.waitForRequest(request => new URL(request.url()).searchParams.get("q") === terms[0]);
    await page.fill("#q", terms[0]);
    await firstRequest;
    const secondResponse = page.waitForResponse(response =>
      new URL(response.url()).searchParams.get("q") === terms[1],
    );
    await page.fill("#q", terms[1]);
    await secondResponse;
    await waitInputValue(page, "#q", terms[1]);
    const escaped = terms[1].replace(/[.*+?^${}()|[\]\\\\]/g, "\\\\$&");
    assert.match(await page.textContent("#results"), new RegExp(escaped), "stale search results won the race");
  });
});

test("e2e: Entity Explorer keeps search visible through delayed page and detail races", { skip: !ENABLED }, async () => {
  await runJourney(browser, "entity-explorer-race", async (page) => {
    await navigateFresh(page, "#/entities");
    await waitVisible(page, "#entity-explorer-query");
    const terms = await page.evaluate(async () => {
      const response = await fetch("/api/entities?q=&limit=10");
      const rows = (await response.json()).rows;
      return rows.map(row => String(row.name_kanji ?? "").slice(0, 2)).filter(Boolean).slice(0, 2);
    });
    assert.equal(terms.length, 2, "need two search terms");
    assert.notEqual(terms[0], terms[1], "search terms must differ");
    await page.route("**/api/entities/explorer?*", async route => {
      const query = new URL(route.request().url()).searchParams.get("q");
      if (query === terms[0]) await new Promise(resolve => setTimeout(resolve, 500));
      await route.continue();
    });
    const firstRequest = page.waitForRequest(request => new URL(request.url()).searchParams.get("q") === terms[0]);
    await page.fill("#entity-explorer-query", terms[0]);
    await page.click("#entity-explorer-form button[type=submit]");
    await firstRequest;
    await waitVisible(page, "#entity-explorer-query");
    const secondResponse = page.waitForResponse(response => new URL(response.url()).searchParams.get("q") === terms[1]);
    await page.fill("#entity-explorer-query", terms[1]);
    await page.click("#entity-explorer-form button[type=submit]");
    await secondResponse;
    await waitInputValue(page, "#entity-explorer-query", terms[1]);
    await waitBodyText(page, terms[1]);
    assert.equal(await page.locator("#entity-explorer-query").isVisible(), true, "search bar disappeared after the race");
  });
});

test("e2e: Entity Explorer ignores a stale delayed entity selection", { skip: !ENABLED }, async () => {
  await runJourney(browser, "entity-explorer-selection-race", async (page) => {
    await navigateFresh(page, "#/entities");
    await waitVisible(page, "#entity-explorer-query");
    await waitCount(page, "[data-entity-select]", 2);
    const ids = await page.$$eval("[data-entity-select]", rows => rows.slice(0, 2).map(row => row.getAttribute("data-entity-select")));
    assert.ok(ids[0] && ids[1] && ids[0] !== ids[1], "need two distinct entities");
    await page.route("**/api/entity/**", async route => {
      const parts = new URL(route.request().url()).pathname.split("/");
      if (parts[3] === ids[0]) await new Promise(resolve => setTimeout(resolve, 500));
      await route.continue();
    });
    await page.locator(`[data-entity-select="${ids[0]}"]`).click();
    await waitCount(page, "[data-entity-select]", 2);
    await page.locator(`[data-entity-select="${ids[1]}"]`).click();
    await waitVisible(page, `[data-selected-entity="${ids[1]}"]`);
    assert.equal(await page.locator(".entity-explorer-detail").getAttribute("data-selected-entity"), ids[1]);
    assert.equal(await page.locator("#entity-explorer-query").isVisible(), true, "search bar disappeared during selection race");
  });
});

test("e2e: Entity Explorer leads with overview and progressive evidence", { skip: !ENABLED }, async () => {
  await runJourney(browser, "entity-explorer-information-hierarchy", async (page) => {
    await navigateFresh(page, "#/entities");
    await waitVisible(page, ".entity-explorer-overview");
    await waitVisible(page, "#entity-explorer-footprint");
    assert.equal(await page.locator("#entity-explorer-observed").getAttribute("open"), null);
    assert.equal(await page.locator(".entity-explorer-map").count(), 0);
    assert.equal(await page.locator("body").textContent().then(text => text.includes("reference_records")), false);
    await page.locator("#entity-explorer-observed summary").click();
    await waitVisible(page, ".entity-explorer-evidence-table");
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
      .filter({ has: page.locator('button.button-check[aria-pressed="false"]') })
      .first();
    await unchecked.waitFor({ state: "visible" });
    const pair = await unchecked.evaluate(el => ({ left: el.getAttribute("data-l") ?? "", right: el.getAttribute("data-r") ?? "" }));
    await unchecked.locator("button.button-check").click();
    const runId = await page.evaluate(() => document.querySelector("#run").value);
    await waitDecision(page, runId, pair.left, 10000);
    await navigateFresh(page, "#/review"); // true reload: decision must survive
    const rowSelector = `tr[data-l="${pair.left}"][data-r="${pair.right}"]`;
    await waitVisible(page, rowSelector);
    const row = page.locator(rowSelector);
    assert.ok(await row.evaluate(el => el.classList.contains("checked")), "decision restored after reload");
    assert.equal(await row.locator("button.button-check").getAttribute("aria-pressed"), "true");
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
    await page.locator(".app-settings > summary").click();
    await page.click("#language");
    await waitLang(page, "en");
    assert.equal(await page.$$eval('[data-nav="entities"]', els => els[0].textContent), "Entity explorer");
    await navigateFresh(page); // true reload: locale must survive
    assert.equal(await page.evaluate(() => document.documentElement.lang), "en", "locale restored from storage");
    await page.fill("#q", term);
    await waitCount(page, "#results .hit", 1);
    const kanjiAfter = await page.textContent("#results .hit h3");
    assert.equal(kanjiAfter, kanjiBefore, "customer data must not be translated");
    assert.match(await page.textContent("main"), /Search existing customers/);
  });
});

test("e2e: data-entry match check returns a server recommendation", { skip: !ENABLED }, async () => {
  await runJourney(browser, "match-evidence", async (page) => {
    await navigateFresh(page, "#/data-entry");
    await waitVisible(page, '[data-field="name_kanji"]');
    await page.fill('[data-field="name_kanji"]', "照合確認 太郎");
    await page.fill('[data-field="phone"]', "090-1111-2222");
    await page.click("#de-check");
    await waitVisible(page, "#de-register:not([disabled])", 120000);
    const output = await page.textContent("#de-match");
    assert.match(output ?? "", /·\s*\d+(?:\.\d+)?%\s*·\s*N\d+/, "server recommendation rendered");
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
    await page.click('[data-admin-section="datasets"]');
    await waitVisible(page, "#dataset-explorer");
    await waitVisible(page, "#dataset-explorer table");
    assert.ok(await page.evaluate(() => !document.querySelector("#nav-admin")?.hasAttribute("hidden")), "admin nav revealed when capable");
    assert.ok(await page.locator("#gen-go").textContent().then(text => /データを生成|Generate data/.test(text ?? "")), "generation action is not discoverable");
    await page.selectOption("#dataset-table", "queries");
    await waitBodyText(page, "入力クエリ", 5000);
    await page.click('[data-admin-section="runtime"]');
    await waitVisible(page, "#runtime-controls");
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
    await waitVisible(page, "#entity-explorer-size");
    await page.selectOption("#entity-explorer-size", "100");
    await waitVisible(page, "#entity-explorer-sort");
    await page.selectOption("#entity-explorer-sort", "name");
    await waitVisible(page, "#view .card a");
    await navigateFresh(page, "#/metrics");
    await waitVisible(page, "#met-unit");
    await page.selectOption("#met-unit", "ratio");
    await waitVisible(page, "#metrics-kpis");
    const ratios = await page.locator("#metrics-kpis .kpi-value").allTextContents();
    assert.ok(ratios.length > 0, "metrics render at least one KPI");
    assert.ok(ratios.every(value => {
      const ratio = Number(value);
      return Number.isFinite(ratio) && ratio >= 0 && ratio <= 1;
    }), "ratio display stays within the metric domain");
  });
});

test("e2e: content panes resize while contextual chrome remains independent", { skip: !ENABLED }, async () => {
  await runJourney(browser, "content-pane-resize", async (page) => {
    await navigateFresh(page, "#/search");
    const searchSash = page.locator('[data-panel-resizer="search-workspace"]');
    await searchSash.waitFor({ state: "visible" });
    await searchSash.press("ArrowRight");
    await waitAttribute(page, '[data-panel-resizer="search-workspace"]', "aria-valuenow", "456");
    const chrome = await page.evaluate(() => {
      const top = document.querySelector(".app-bar").getBoundingClientRect();
      return {
        topPosition: getComputedStyle(document.querySelector(".app-bar")).position,
        topAtViewport: top.top,
        statusPosition: getComputedStyle(document.querySelector("#status-strip")).position,
        desktopNavToggle: getComputedStyle(document.querySelector("#nav-toggle")).display,
      };
    });
    assert.equal(chrome.topPosition, "sticky");
    assert.ok(chrome.topAtViewport <= 1, "top bar is not anchored to the viewport");
    assert.notEqual(chrome.statusPosition, "fixed", "system diagnostics must remain contextual");
    assert.notEqual(chrome.desktopNavToggle, "none", "desktop navigation control is unavailable");
    assert.equal(await page.locator(".app-bar [data-panel-resizer]").count(), 0);
    assert.equal(await page.locator("#status-strip [data-panel-resizer]").count(), 0);

    await navigateFresh(page, "#/entities");
    const entitySash = page.locator('[data-panel-resizer="entity-explorer"]');
    await entitySash.waitFor({ state: "visible" });
    await entitySash.press("ArrowRight");
    await waitAttribute(page, '[data-panel-resizer="entity-explorer"]', "aria-valuenow", "536");

    await navigateFresh(page, "#/review");
    await waitVisible(page, "table tr[data-l][data-r]");
    const pair = await page.locator("table tr[data-l][data-r]").first().evaluate(row => ({
      left: row.getAttribute("data-l"),
      right: row.getAttribute("data-r"),
    }));
    await navigateFresh(page, `#/compare/${pair.left}/${pair.right}`);
    const compareSash = page.locator('[data-panel-resizer="compare"]');
    await compareSash.waitFor({ state: "visible" });
    await compareSash.press("ArrowLeft");
    await waitAttribute(page, '[data-panel-resizer="compare"]', "aria-valuenow", "424");
  });
});

test("e2e: navigation sash resizes with pointer input and survives collapse", { skip: !ENABLED }, async () => {
  await runJourney(
    browser,
    "navigation-resize",
    async (page) => {
      await navigateFresh(page, "#/metrics");
      const sash = page.locator("#nav-resizer");
      const box = await sash.boundingBox();
      assert.ok(box, "navigation sash is not rendered");
      const startX = box.x + box.width / 2;
      const startY = box.y + box.height / 2;
      await page.mouse.move(startX, startY);
      await page.mouse.down();
      await page.mouse.move(startX + 64, startY);
      await page.mouse.up();
      await waitAttribute(page, "#nav-resizer", "aria-valuenow", "320");
      await waitComputedStyle(page, ".shell", "grid-template-columns", "320px");
      await page.locator("#nav-toggle").click();
      await waitAttribute(page, "#nav-toggle", "aria-expanded", "false");
      await page.locator("#nav-toggle").click();
      await waitAttribute(page, "#nav-toggle", "aria-expanded", "true");
      assert.equal(await sash.getAttribute("aria-valuenow"), "320", "sidebar width was not preserved");
    },
  );
});

test("e2e: mobile viewport + reduced motion — no page-level overflow, chrome usable", { skip: !ENABLED }, async () => {
  await runJourney(
    browser,
    "mobile-reduced-motion",
    async (page) => {
      await navigateFresh(page);
      const metrics = await page.evaluate(() => ({
        overflow: document.documentElement.scrollWidth - window.innerWidth,
        navOverflow: getComputedStyle(document.querySelector(".side")).overflowY,
        navExpanded: document.querySelector("#nav-toggle").getAttribute("aria-expanded"),
        langBtn: !!document.querySelector("#language"),
      }));
      assert.ok(metrics.overflow <= 1, `page overflows horizontally by ${metrics.overflow}px`);
      assert.match(metrics.navOverflow, /auto|scroll/, "side nav uses a bounded mobile drawer");
      assert.equal(metrics.navExpanded, "false", "mobile navigation starts closed");
      assert.ok(metrics.langBtn, "language toggle present on mobile");
      await page.click("#nav-toggle");
      await waitAttribute(page, "#nav-toggle", "aria-expanded", "true");
      assert.equal(await page.locator("#primary-navigation").getAttribute("aria-hidden"), "false");
      await page.keyboard.press("Escape");
      await waitAttribute(page, "#nav-toggle", "aria-expanded", "false");
      await page.locator(".app-settings > summary").click();
      await page.click("#language");
      await waitLang(page, "en");
    },
    { viewport: { width: 390, height: 844 }, reducedMotion: "reduce" },
  );
});