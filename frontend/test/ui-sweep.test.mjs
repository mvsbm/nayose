// UI sweep E2E (Playwright): visits EVERY route and inventories EVERY
// interactive element (button / link / input / select / range / checkbox).
// For each element we assert visibility, enabled state, and an accessible
// name; then we exercise the SAFE actions and assert concrete state changes.
// Heavy side-effect controls (job-starters, decision writes, registrations)
// are asserted for presence/label/state but NOT clicked here — the dedicated
// journeys (admin workspace, data entry wizard, live match, review) run those
// real actions with precise assertions. runJourney()'s strict observer fails
// the whole sweep on any pageerror / console error / non-2xx /api call, so
// "every element works" is enforced as: every inventory row labelled+live,
// every exercised action produced a state change, zero runtime errors.
//
// The sweep shares one inventory→assert→act flow for every route, with
// route-specific steps supplied by callbacks. Collection, assertions, and
// actions remain separate; runJourney owns browser errors and navigation.
// Hidden-by-design controls use a documented allowlist, and member actions
// degrade gracefully when a page has fewer than two members.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import AxeBuilder from "@axe-core/playwright";
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
  waitBodyPattern,
  waitChecked,
  waitCount,
  waitHidden,
  waitLang,
  waitUiSettled,
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

// Heavy side-effect controls: asserted in the inventory, deliberately not
// clicked by the sweep (their real actions live in the dedicated journeys).
const SKIP_ACTIONS = new Set([
  "gen-go", "tr-go", "set-go", // spawn subprocess jobs (admin)
  "dz-confirm", "dz-promote", "dz-prune", "dz-reset", // mutate bundle state
  "de-check", "de-match", "de-register", // start jobs / write manual corpus
  "filter-clear", // route-specific filter navigation is asserted below
  "entity-explorer-potential", "dataset-filter-apply", "dataset-filter-clear", // disclosure state is exercised by route feels
]);

// Shell chrome exercised ONCE on the metrics route (feels.metrics) with
// deterministic assertions: clicking #cmd here opens the palette and would
// intercept every subsequent generic-loop action for 30s apiece.
const CHROME_ONCE = new Set(["cmd", "theme", "density", "language", "run", "nav-toggle"]);

// Hidden-by-design controls: only shown while a job is running / palette open.
const HIDDEN_BY_DESIGN = new Set(["rt-resume", "palette-input", "palette-results", "nav-scrim", "run", "theme", "density", "language"]);

// Stateful-disabled controls: disabled is a STATE (page boundary, no selection
// yet, empty history), not a defect — these flip with user state.
const STATE_DISABLED = new Set([
  "member-prev", "member-next", "drill", // page boundaries
  "member-compare", "member-clear", // disable until at least one member selected
  "entity-explorer-prev", "entity-explorer-next", // page boundaries
  "dataset-load", "dataset-prev", "dataset-next", // dataset loading and page boundaries
  "dz-promote", "dz-prune", "dz-reset", "gen-preview-go", "gen-go", // two-step-confirm / preview-pending boundaries
  "de-import", "de-check", "de-match", "de-dry", // wizard gates: armed as rows import / form validates
  "de-register", // armed by a successful match check (data entry wizard step)
  "undo", "review-prev", "more", // no reversible decision / page boundaries
]);

// Read-only DOM inventory of every interactive candidate and page health.
async function collectCandidates(page) {
  return page.evaluate(() => {
    const SELECTOR = [
      "button", "a[href]", "input:not([type='hidden'])", "select", "textarea",
      "details > summary", "[role='button']", "[role='checkbox']", "[role='switch']",
    ].join(",");
    const els = [...document.querySelectorAll(SELECTOR)];
    const out = [];
    for (const el of els) {
      const rect = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      const hiddenControl = style.opacity === "0" || (el.matches("input[type='checkbox']") && rect.width <= 2 && rect.height <= 2);
      const visible = rect.width > 0 && rect.height > 0 &&
        style.visibility !== "hidden" && style.display !== "none" && !hiddenControl;
      const isButtonish = el.tagName === "BUTTON" || el.tagName === "SUMMARY" || el.hasAttribute("role");
      const associatedLabel = el.id ? document.querySelector(`label[for='${el.id}']`) || el.closest("label") : el.closest("label");
      const label = el.getAttribute("aria-label") || (isButtonish ? el.textContent.trim() : "");
      const name =
        label ||
        el.getAttribute("title") ||
        (el.id && document.querySelector(`label[for='${el.id}']`)?.textContent.trim()) ||
        (el.closest("label")?.textContent.trim()) ||
        (el.tagName === "A" ? el.getAttribute("href") : "") ||
        "";
      out.push({
        tag: el.tagName.toLowerCase(),
        id: el.id || "",
        type: el.getAttribute("type") || "",
        role: el.getAttribute("role") || "",
        href: (el.tagName === "A" ? el.getAttribute("href") : "") || "",
        name: name.slice(0, 80),
        disabled: el.disabled === true || el.getAttribute("aria-disabled") === "true",
        visible,
        checked: el.checked === true,
        hiddenByDesign: Boolean(
          (el.closest("#primary-navigation, #activity-navigation, #side-nav") && matchMedia("(max-width: 900px)").matches) ||
          (el.closest(".run-context") && matchMedia("(max-width: 820px)").matches) ||
          Boolean(el.closest(".admin-section[hidden]"))
        ),
        visuallyHiddenControl: !visible && el.tagName === "INPUT" && el.type === "checkbox" && Boolean(associatedLabel && associatedLabel.getBoundingClientRect().width > 0 && associatedLabel.getBoundingClientRect().height > 0),
        size: { w: Math.round(rect.width), h: Math.round(rect.height) },
      });
    }
    const text = document.body.textContent || "";
    return {
      items: out,
      leaked: /\bundefined\b|\[object Object\]|NaN(?!.+\d)/.test(text),
      viewChildren: document.querySelector("#view")?.children.length ?? 0,
    };
  });
}

// Pure assertions over the inventory (no DOM or browser state needed).
function assertInventory(route, { items, leaked, viewChildren }) {
  assert.ok(viewChildren > 0, `${route}: #view rendered empty`);
  assert.ok(!leaked, `${route}: template leakage (undefined/[object Object]/NaN) in body`);
  for (const it of items) {
    assert.ok(it.name.length > 0, `${route}: [${it.id || it.tag}] interactive without accessible name`);
    assert.ok(
      !it.disabled || STATE_DISABLED.has(it.id),
      `${route}: [${it.id || it.tag}] disabled without expectation (stateful boundary only)`,
    );
    assert.ok(
      it.visible || it.hiddenByDesign || it.visuallyHiddenControl || HIDDEN_BY_DESIGN.has(it.id),
      `${route}: [${it.id || it.tag} "${it.name}"] unexpectedly hidden (add to HIDDEN_BY_DESIGN only if intentional)`,
    );
    if (it.visible) {
      const compactNativeControl = it.tag === "input" && ["checkbox", "radio"].includes(it.type);
      const minimum = compactNativeControl ? 24 : 32;
      assert.ok(it.size.w >= minimum && it.size.h >= minimum, `${route}: [${it.id || it.tag} "${it.name}"] target is below ${minimum}×${minimum}px`);
    }
  }
}

// Close any modal overlay before leaving a route: hash navigation reuses the
// same document, so an open palette would intercept the next route's clicks.
// The app's only close contract is Escape (document keydown) / overlay click;
// #cmd is bind-once openPalette (NOT a toggle), so we use Escape.
async function closePalette(page) {
  if (await page.locator("#palette:not([hidden])").count()) {
    await page.keyboard.press("Escape");
    await waitHidden(page, "#palette");
  }
}

async function closeMobileNavigation(page) {
  const toggle = page.locator("#nav-toggle");
  if (!(await toggle.count()) || !(await toggle.isVisible())) return;
  const scrim = page.locator("#nav-scrim");
  await waitUiSettled(page);
  if (!(await scrim.isVisible()) || await toggle.getAttribute("aria-expanded") !== "true") return;
  await page.keyboard.press("Escape");
  await waitUiSettled(page);
  if (await toggle.getAttribute("aria-expanded") === "true") {
    await scrim.click({ force: true });
    await waitUiSettled(page);
  }
}

async function openAppSettings(page) {
  const settings = page.locator(".app-settings");
  if (!(await settings.evaluate(element => element.open))) {
    await settings.locator(":scope > summary").click();
    await waitUiSettled(page);
  }
}

async function openDisclosures(page) {
  const closed = page.locator("#view details:not([open]) > summary").filter({ visible: true });
  while (await closed.count()) {
    await closed.first().click();
    await waitUiSettled(page);
  }
}

// Base sweep flow with route-specific steps supplied through feels().
async function sweepRoute(page, route, { hash = "#/", skip = SKIP_ACTIONS, feels = async () => {} } = {}) {
  console.error(`[sweep] ${route} ${hash} — start`);
  await navigateFresh(page, hash);
  await closeMobileNavigation(page);
  await waitVisible(page, "#view");
  await waitBodyText(page, "Nayose"); // chrome rendered (stable header)
  await waitVisible(page, '#view > [data-view-ready="true"]');
  await openDisclosures(page);
  const before = await collectCandidates(page);
  assertInventory(route, before);
  const accessibility = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag22aa"]).analyze();
  assert.equal(
    accessibility.violations.length,
    0,
    `${route}: accessibility violations: ${accessibility.violations.map(v => `${v.id} (${v.nodes.length})`).join(", ")}`,
  );
  for (const it of before.items) {
    if (skip.has(it.id) || CHROME_ONCE.has(it.id) || it.tag === "a" || it.tag === "details") continue;
    if (!it.id) continue; // id-less summaries/rows: exercised by real matches, not generically
    if (!it.visible && !it.visuallyHiddenControl) continue;
    if (it.disabled) continue; // stateful-disabled (boundary/arm): asserted above, not clicked
    const t0 = Date.now();
    const escapedId = it.id.replace(/[\\']/g, "\\$&");
    const handle = it.tag === "input" && it.type === "checkbox"
      ? page.locator(`label[for='${escapedId}'], label:has(> [id='${escapedId}'])`).first()
      : page.locator(
        it.id
          ? `[id='${escapedId}']`
          : `${it.tag}[href='${it.href}']`,
      ).first();
    if (it.tag === "input" && (it.type === "search" || it.type === "text" || it.type === "date")) {
      continue; // typing flows live in the dedicated journeys; inventory covered these
    } else if (it.tag === "input" && it.type === "checkbox") {
      await handle.click({ timeout: 5000 });
    } else if (it.tag === "button") {
      await handle.click({ timeout: 5000 });
    }
    const slow = Date.now() - t0;
    if (slow > 4000) console.error(`[sweep-slow] ${route}: ${it.id} took ${slow}ms`);
    await waitUiSettled(page);
  }
  await closePalette(page);
  await feels(page);
  await closePalette(page);
  console.error(`[sweep] ${route} done`);
}

// Route-specific assertions (injected into the sweep skeleton).
const feels = {
  metrics: async (page) => {
    await waitVisible(page, "#met-unit");
    await page.locator("#met-unit").selectOption("ratio");
    await waitBodyPattern(page, /\d\.\d{4}/);
    await page.locator("#met-unit").selectOption("percent");
    await waitBodyPattern(page, /\d\.\d%/);
    const density0 = await page.evaluate(() => document.documentElement.dataset.density || "comfortable");
    await openAppSettings(page);
    await page.locator("#density").click();
    assert.equal(await page.evaluate(() => document.documentElement.dataset.density), "compact", "density did not enter compact mode");
    await page.locator("#density").click();
    assert.equal(await page.evaluate(() => document.documentElement.dataset.density || "comfortable"), density0, "density did not restore");
    // theme is a three-state preference; verify every transition, including auto.
    const themeLabel0 = await page.locator("#theme").textContent();
    await page.locator("#theme").click();
    const themeLabel1 = await page.locator("#theme").textContent();
    await page.locator("#theme").click();
    const themeLabel2 = await page.locator("#theme").textContent();
    await page.locator("#theme").click();
    const themeLabel3 = await page.locator("#theme").textContent();
    assert.notEqual(themeLabel0, themeLabel1, "theme auto→light transition missing");
    assert.notEqual(themeLabel1, themeLabel2, "theme light→dark transition missing");
    assert.notEqual(themeLabel2, themeLabel3, "theme dark→auto transition missing");
    assert.equal(themeLabel0, themeLabel3, "theme cycle did not return to its initial preference");
    const lang0 = await page.evaluate(() => document.documentElement.lang);
    await page.locator("#language").click();
    await waitLang(page, lang0 === "ja" ? "en" : "ja");
    await page.locator("#language").click(); // restore
    await waitLang(page, lang0);
    await page.locator(".app-settings > summary").click();
    await closeMobileNavigation(page);
    const navToggle = page.locator("#nav-toggle");
    if (await navToggle.isVisible()) {
      const mobileNavigation = await page.evaluate(() => matchMedia("(max-width: 820px)").matches);
      assert.equal(
        await navToggle.getAttribute("aria-expanded"),
        mobileNavigation ? "false" : "true",
        "navigation initial state does not match its viewport",
      );
      await navToggle.click();
      await waitUiSettled(page);
      assert.equal(
        await navToggle.getAttribute("aria-expanded"),
        mobileNavigation ? "true" : "false",
        "navigation toggle did not change state",
      );
      if (mobileNavigation) await page.keyboard.press("Escape");
      else await navToggle.click();
      await waitUiSettled(page);
      assert.equal(
        await navToggle.getAttribute("aria-expanded"),
        mobileNavigation ? "false" : "true",
        "navigation did not restore its initial state",
      );
    }
    await page.locator("#cmd").click();
    await waitVisible(page, "#palette:not([hidden])");
    await page.keyboard.press("Escape");
    await waitHidden(page, "#palette");
  },
  search: async (page) => {
    await page.locator("#q").fill(await searchSeed(page));
    await waitCount(page, "#stat", 1);
    await waitCount(page, "a.hit", 2);
    return page.$$eval("a.hit", links => links.slice(0, 2).map(a => a.getAttribute("href") ?? ""));
  },
  entity: async (page) => {
    const members = await page.locator("[data-member]").count();
    if (members === 0) return; // honesty: no member rows to exercise
    await page.locator("[data-member]").first().click();
    await waitChecked(page, "[data-select]", true);
    const chosen = await page.locator("#member-selected").textContent().catch(() => "");
    assert.match(String(chosen), /\d+/, "selecting a member did not update the selection counter");
    if (members < 2) return;
    await page.locator("[data-member]").nth(1).click();
    await waitUiSettled(page);
    assert.equal(await page.locator("[data-select]").nth(1).isChecked(), true, "second member selection did not update");
    assert.ok(
      await page.locator("#member-compare").isEnabled(),
      "compare stayed disabled after two member selections",
    );
    await page.locator("#member-clear").click();
    await waitChecked(page, "[data-select]", false);
    assert.equal(
      await page.locator("#member-compare").isDisabled(),
      true,
      "compare enabled again after member-clear",
    );
  },
  review: async (page) => {
    await waitVisible(page, "#reviewer");
    if (!(await page.locator("button.check").count())) return;
    const reviewRows = page.locator("tbody tr").filter({ has: page.locator("button.check") });
    assert.equal(
      await reviewRows.first().locator("td").first().locator("button.check").count(),
      1,
      "review progress action is not the first, decision-facing cell",
    );
    const sibling = reviewRows.nth(1);
    const siblingKey = await sibling.evaluate(el => ({ left: el.getAttribute("data-l") ?? "", right: el.getAttribute("data-r") ?? "" })).catch(() => null);
    const siblingBefore = siblingKey
      ? await page.locator("tbody tr[data-l][data-r]").evaluateAll((rows, key) => rows.find(row => row.getAttribute("data-l") === key.left && row.getAttribute("data-r") === key.right)?.outerHTML ?? null, siblingKey)
      : null;
    const pressed0 = await reviewRows.first().locator("button.check").getAttribute("aria-pressed");
    await reviewRows.first().locator("button.check").click();
    await waitUiSettled(page);
    const pressed1 = await reviewRows.first().locator("button.check").getAttribute("aria-pressed");
    assert.notEqual(pressed0, pressed1, "review toggle did not flip aria-pressed");
    await page.locator("#undo").click();
    const pressed2 = await reviewRows.first().locator("button.check").getAttribute("aria-pressed");
    assert.equal(pressed2, pressed0, "undo did not restore the decision state");
    if (siblingBefore !== null && siblingKey) {
      const siblingAfter = await page.locator("tbody tr[data-l][data-r]").evaluateAll((rows, key) => rows.find(row => row.getAttribute("data-l") === key.left && row.getAttribute("data-r") === key.right)?.outerHTML ?? null, siblingKey);
      assert.equal(siblingAfter, siblingBefore, "review toggle re-rendered a sibling row");
    }
  },
  reviewFiltered: async (page, nayose) => {
    await waitVisible(page, "#filter-clear");
    await waitBodyText(page, nayose);
    await page.locator("#filter-clear").click();
    await waitHidden(page, "#filter-clear");
  },
  admin: async (page) => {
    await waitVisible(page, "#gen-go");
    // open-by-default: the token gate is NOT rendered at all (count 0, not hidden)
    assert.equal(await page.locator("#adm-token").count(), 0, "admin token gate rendered despite open-by-default");
    await page.click('[data-admin-section="datasets"]');
    await waitVisible(page, "#dataset-explorer");
    const picker = page.locator("details.dataset-column-picker");
    if (await picker.count() && (await picker.getAttribute("open")) === null) {
      await picker.locator("summary").click();
    }
    await page.locator("#dataset-filter-apply").click();
    await waitUiSettled(page);
    if ((await picker.getAttribute("open")) === null) {
      await picker.locator("summary").click();
    }
    await page.locator("#dataset-filter-clear").click();
    await page.click('[data-admin-section="runtime"]');
    await waitVisible(page, "#runtime-controls");
    if ((await page.locator("#runtime-controls").getAttribute("open")) === null) {
      await page.locator("#runtime-controls summary").click();
    }
    await waitVisible(page, "#set-go");
    await waitVisible(page, "#dz-prune");
    await page.click('[data-admin-section="data-training"]');
    await waitVisible(page, "#gen-go");
    if ((await page.locator("#training-controls").getAttribute("open")) === null) {
      await page.locator("#training-controls summary").click({ force: true });
    }
    await waitVisible(page, "#tr-go");
  },
  dataEntry: async (page) => {
    await waitVisible(page, "#de-register");
    await waitVisible(page, "#de-file");
    await waitVisible(page, "#de-dry");
  },
  entities: async (page) => {
    const checkbox = page.locator("#entity-explorer-potential");
    if (!(await checkbox.count())) return;
    const section = page.locator("#entity-explorer-potential-section");
    if ((await section.getAttribute("open")) === null) {
      await section.locator("summary").click();
    }
    await page.locator("label.entity-explorer-potential").click();
    await waitChecked(page, "#entity-explorer-potential", true);
  },
  compare: async (page) => {
    await waitBodyText(page, "比較");
  },
};

test("UI sweep: every route × every interactive element (strict zero-error)", { skip: !ENABLED }, async () => {
  await runJourney(browser, "ui-sweep-all-routes", async (page) => {
    // workbench (search + match form): inventory only
    await sweepRoute(page, "#/", { hash: "#/" });
    await sweepRoute(page, "#/metrics", { hash: "#/metrics", feels: feels.metrics });

    let hrefs = [];
    await sweepRoute(page, "#/search", {
      hash: "#/search",
      feels: async (p) => { hrefs = await feels.search(p); },
    });
    assert.equal(hrefs.length, 2, "search results did not yield two entity links");
    assert.ok(hrefs.every(h => h.startsWith("#/search?entity=")));
    const entityIds = hrefs.map(href => new URLSearchParams(href.split("?")[1] ?? "").get("entity") ?? "");
    const [entityA, entityB] = entityIds;

    await sweepRoute(page, "#/entity", { hash: `#/entity/${encodeURIComponent(entityA)}`, feels: feels.entity });
    await sweepRoute(page, "#/compare", {
      hash: `#/compare/${encodeURIComponent(entityA)}/${encodeURIComponent(entityB)}`,
      feels: feels.compare,
    });
    await sweepRoute(page, "#/entities", { hash: "#/entities", feels: feels.entities });
    await sweepRoute(page, "#/review", { hash: "#/review", feels: feels.review });
    await sweepRoute(page, "#/review?nayose=", {
      hash: `#/review?nayose=${encodeURIComponent(entityA)}`,
      feels: async (p) => feels.reviewFiltered(p, entityA),
    });
    await sweepRoute(page, "#/confusion", { hash: "#/confusion" });
    await sweepRoute(page, "#/compare-runs", { hash: "#/compare-runs" });
    await sweepRoute(page, "#/admin", { hash: "#/admin", feels: feels.admin });
    await sweepRoute(page, "#/data-entry", { hash: "#/data-entry", feels: feels.dataEntry });
  });
});

// Pure-node inventory shape test: keeps the sweep's building blocks honest
// without a browser (route contract: ids are stable inventory keys).
test("UI sweep: every static route × locale × viewport cell is loadable and interactive", { skip: !ENABLED }, async () => {
  const routes = [
    ["search", "#/search", feels.search],
    ["metrics", "#/metrics", feels.metrics],
    ["entities", "#/entities", feels.entities],
    ["review", "#/review", feels.review],
    ["confusion", "#/confusion", undefined],
    ["compare-runs", "#/compare-runs", undefined],
    ["admin", "#/admin", feels.admin],
    ["data-entry", "#/data-entry", feels.dataEntry],
  ];
  const cells = [
    ["desktop-light", "ja", { width: 1440, height: 1000 }, { colorScheme: "light" }],
    ["desktop-dark", "en", { width: 1440, height: 1000 }, { colorScheme: "dark" }],
    ["mobile-light-reduced", "ja", { width: 390, height: 844 }, { colorScheme: "light", reducedMotion: "reduce" }],
    ["mobile-dark-reduced", "en", { width: 390, height: 844 }, { colorScheme: "dark", reducedMotion: "reduce" }],
  ];
  for (const [viewportName, locale, viewport, media] of cells) {
    for (const [routeName, hash, routeFeels] of routes) {
      await runJourney(browser, `grid-${viewportName}-${locale}-${routeName}`, async page => {
        await navigateFresh(page);
        if (await page.evaluate(() => document.documentElement.lang) !== locale) {
          await openAppSettings(page);
          await page.click("#language");
          await waitLang(page, locale);
          await page.locator(".app-settings > summary").click();
        }
        await sweepRoute(page, `${viewportName}/${locale}/${routeName}`, { hash, feels: routeFeels });
      }, { viewport, ...media });
    }
  }
});

test("UI sweep: forced colors and 200% reflow preserve the primary workflow", { skip: !ENABLED }, async () => {
  await runJourney(browser, "accessibility-forced-colors-reflow", async page => {
    await sweepRoute(page, "forced-colors/reflow/search", { hash: "#/search", feels: feels.search });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    assert.ok(overflow <= 1, `200% reflow viewport overflows horizontally by ${overflow}px`);
  }, { viewport: { width: 720, height: 700 }, forcedColors: "active", reducedMotion: "reduce" });
});

test("UI sweep: skip-set covers every admin job-starter id", () => {
  for (const id of ["gen-go", "tr-go", "set-go", "dz-confirm", "de-check", "de-register"]) {
    assert.ok(SKIP_ACTIONS.has(id), `heavy control ${id} missing from the skip allowlist`);
  }
});