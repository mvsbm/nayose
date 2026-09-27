import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import {
  cleanupFixture,
  ENABLED,
  FIXTURE,
  launchBrowser,
  navigateFresh,
  runJourney,
  startServer,
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

function budgetFromEnvironment() {
  return {
    routeReadyMs: Number(process.env.NAYOSE_FRONTEND_ROUTE_BUDGET_MS ?? 5000),
    jsBytes: Number(process.env.NAYOSE_FRONTEND_JS_BUDGET_BYTES ?? 1_500_000),
    domNodes: Number(process.env.NAYOSE_FRONTEND_DOM_BUDGET ?? 2500),
    longTasks: Number(process.env.NAYOSE_FRONTEND_LONG_TASK_BUDGET ?? 0),
  };
}

async function measureRoute(path) {
  let metrics;
  await runJourney(browser, `frontend-performance-${path.replace(/[^a-z0-9]+/gi, "-")}`, async page => {
    await navigateFresh(page, path);
    metrics = await page.evaluate(() => {
      const route = performance.getEntriesByType("measure")
        .filter(entry => entry.name.startsWith("nayose:route-ready-"))
        .at(-1);
      const js = performance.getEntriesByType("resource")
        .filter(entry => entry.name.endsWith(".js"))
        .reduce((total, entry) => total + (entry.transferSize || entry.encodedBodySize || 0), 0);
      return {
        route_ready_ms: route?.duration ?? Number.NaN,
        js_bytes: js,
        dom_nodes: document.querySelectorAll("*").length,
        long_tasks: performance.getEntriesByType("longtask").length,
      };
    });
    console.log(JSON.stringify({ kind: "frontend_performance", path, ...metrics }));
  });
  return metrics;
}

test("frontend performance stays within the configured browser budgets", { skip: !ENABLED }, async () => {
  const budget = budgetFromEnvironment();
  for (const path of ["#/", "#/review", "#/entities", "#/metrics"]) {
    const metrics = await measureRoute(path);
    assert.ok(Number.isFinite(metrics.route_ready_ms), `${path}: route readiness measurement is missing`);
    assert.ok(metrics.route_ready_ms <= budget.routeReadyMs, `${path}: route readiness exceeded ${budget.routeReadyMs}ms`);
    assert.ok(metrics.js_bytes <= budget.jsBytes, `${path}: JavaScript resources exceeded ${budget.jsBytes} bytes`);
    assert.ok(metrics.dom_nodes <= budget.domNodes, `${path}: DOM exceeded ${budget.domNodes} nodes`);
    assert.ok(metrics.long_tasks <= budget.longTasks, `${path}: long tasks exceeded ${budget.longTasks}`);
  }
});
