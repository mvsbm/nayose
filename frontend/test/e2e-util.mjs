// E2E infrastructure (Playwright): the only entry point to browser automation.
// Journeys use these helpers instead of raw browser primitives. This module
// owns waits, navigation, fresh contexts, and transport observation. Bundles
// are copied to a temporary root so runtime artifacts are never mutated.
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import {
  copyFileSync,
  cpSync,
  createWriteStream,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  realpathSync,
  renameSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { join, basename, dirname } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright";

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), "..");
const ROOT = join(FRONTEND, "..");
const PYTHON = existsSync(join(ROOT, ".venv", "bin", "python"))
  ? join(ROOT, ".venv", "bin", "python")
  : "python";

export const PORT = Number(process.env.NAYOSE_E2E_PORT ?? 8777);
export const BASE = `http://127.0.0.1:${PORT}`;
export const ENABLED = process.env.NAYOSE_E2E === "1";
const E2E_METRICS = {
  journeys: 0,
  journey_ms: 0,
  api_requests: 0,
  api_responses: 0,
  api_failures: 0,
  api_aborted: 0,
  api_errors: 0,
  request_bytes: 0,
  response_bytes: 0,
};

// Resolve the "current" target of a models/data root, tolerating a direct dir.
function layoutSource(rootPath) {
  const linked = rootPath && existsSync(join(rootPath, "current"));
  const target = linked ? join(rootPath, "current") : rootPath;
  if (!target || !existsSync(target)) return null;
  const src = realpathSync(target);
  return { src, name: basename(src) };
}

function sha256(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

function fingerprint12(dir, file) {
  try {
    const meta = JSON.parse(readFileSync(join(dir, file), "utf8"));
    return String(meta.data_fingerprint ?? "").slice(0, 12);
  } catch {
    return "";
  }
}

function prepare() {
  const envModels = process.env.NAYOSE_E2E_MODELS || join(ROOT, "models");
  const envData = process.env.NAYOSE_E2E_DATA || join(ROOT, "data");
  const run = layoutSource(envModels);
  const gen = layoutSource(envData);
  if (!run || !gen) {
    throw new Error(
      "E2E needs a trained bundle: set NAYOSE_E2E_MODELS/NAYOSE_E2E_DATA, or train and point models/current + data/current first (nayose model train).",
    );
  }
  // fingerprint contract: the copied pair must MATCH (a mismatched copy fails
  // fast here, not as a 10-minute engine-load timeout). If data/current
  // drifted, pick the last matching generation under the data root.
  const fp = fingerprint12(run.src, "run_manifest.json");
  if (fp) {
    const generationsRoot = join(envData, "generations");
    const candidates = existsSync(generationsRoot)
      ? readdirSync(generationsRoot).map(n => join(generationsRoot, n))
      : [];
    const currentMatches = fingerprint12(gen.src, "generation_manifest.json") === fp;
    const pick = candidates.find(dir => fingerprint12(dir, "generation_manifest.json") === fp);
    if (currentMatches && (!pick || gen.name === basename(pick))) {
      // already consistent: keep data/current as-is
    } else if (pick) {
      gen.src = pick;
      gen.name = basename(pick);
    } else {
      throw new Error(
        `bundle ${run.name} (fingerprint ${fp}) matches no generation under ${envData}; ` +
        `point NAYOSE_E2E_DATA at the trained generation`,
      );
    }
  }
  const root = mkdtempSync(join(tmpdir(), "nayose-e2e-"));
  const models = join(root, "models");
  const data = join(root, "data");
  mkdirSync(join(models, "runs"), { recursive: true });
  cpSync(run.src, join(models, "runs", run.name), { recursive: true });
  symlinkSync(join("runs", run.name), join(models, "current"), "dir");
  cpSync(gen.src, data, { recursive: true });
  mkdirSync(join(root, "generations"), { recursive: true });
  cpSync(gen.src, join(root, "generations", gen.name), { recursive: true });
  const modelManifest = join(models, "runs", run.name, "run_manifest.json");
  const dataManifest = join(root, "generations", gen.name, "generation_manifest.json");
  const dataPayload = JSON.parse(readFileSync(dataManifest, "utf8"));
  writeFileSync(join(root, "release.json"), JSON.stringify({
    release_id: "rel_e2e",
    data_generation_id: gen.name,
    model_run_id: run.name,
    data_fingerprint: dataPayload.data_fingerprint,
    data_manifest_sha256: sha256(dataManifest),
    model_manifest_sha256: sha256(modelManifest),
    published_at_utc: "2026-01-01T00:00:00Z",
  }));
  return { root, models, data };
}

export function startServer(models, data, port = PORT) {
  return new Promise((resolve, reject) => {
    const logPath = FIXTURE.root ? join(FIXTURE.root, "server.log") : null;
    const proc = spawn(PYTHON, ["-m", "api.main"], {
      cwd: ROOT,
      env: {
        ...process.env,
        NAYOSE_MODELS_DIR: models,
        NAYOSE_DATA_DIR: data,
        NAYOSE_DASH_PORT: String(port),
        NAYOSE_DATA_ROOT: FIXTURE.root, // admin generate targets the isolated fixture
        NAYOSE_MANUAL_DIR: join(FIXTURE.root, "manual"), // data entry lives in the fixture
        NAYOSE_WARM_EMBED: "1", // warm the engine at boot: deterministic cold-start cost
      },
      stdio: ["ignore", "ignore", "pipe"],
    });
    if (logPath) proc.stderr.pipe(createWriteStream(logPath, { flags: "a" }));
    // the fixture server warms the 374k engine in lifespan (~2min) before readiness
    const deadline = Date.now() + 240000;
    const poll = async () => {
      try {
        const res = await fetch(`${BASE}/api/runs`);
        if (res.ok) return resolve(proc);
      } catch {}
      if (Date.now() > deadline) {
        proc.kill();
        return reject(new Error("api server did not become healthy; check the bundle fixture"));
      }
      setTimeout(poll, 250);
    };
    poll();
  });
}

export const FIXTURE = ENABLED ? prepare() : { root: null, models: null, data: null };

export async function launchBrowser() {
  return chromium.launch();
}

async function waitBoot(page) {
  // Navigation and view data are separate async boundaries. Wait for the
  // application-owned readiness marker so callers never inspect the active
  // container before route listeners and data loading have completed.
  await page.waitForSelector("#view > [data-view-ready=\"true\"]", { timeout: 15000 });
}

// Deterministic navigation primitive: a changing PATH query always produces a
// fresh document (reload()/hash-only can silently keep stale state).
export async function navigateFresh(page, hashPath = "/") {
  await page.goto(`${BASE}/?boot=${Date.now()}${hashPath}`, { waitUntil: "load" });
  await waitBoot(page);
}

export async function waitVisible(page, selector, timeout = 15000) {
  await page.waitForSelector(selector, { state: "visible", timeout });
}

export async function waitBodyText(page, needle, timeout = 15000) {
  await page.waitForFunction((text) => document.body.textContent.includes(text), needle, { timeout });
}

export async function waitAttribute(page, selector, attribute, value, timeout = 15000) {
  await page.waitForFunction(
    ({ selector, attribute, value }) => document.querySelector(selector)?.getAttribute(attribute) === value,
    { selector, attribute, value },
    { timeout },
  );
}

export async function waitComputedStyle(page, selector, property, includes, timeout = 15000) {
  await page.waitForFunction(
    ({ selector, property, includes }) => getComputedStyle(document.querySelector(selector))[property].includes(includes),
    { selector, property, includes },
    { timeout },
  );
}

export async function waitHash(page, prefix, timeout = 15000) {
  await page.waitForFunction((p) => location.hash.startsWith(p), prefix, { timeout });
}

export async function waitLang(page, lang, timeout = 15000) {
  await page.waitForFunction((l) => document.documentElement.lang === l, lang, { timeout });
}

export async function waitCount(page, selector, min, timeout = 30000) {
  await page.waitForFunction(
    ({ selector, min }) => document.querySelectorAll(selector).length >= min,
    { selector, min },
    { timeout },
  );
}

export async function waitInputValue(page, selector, value, timeout = 15000) {
  await page.waitForFunction(
    ({ selector, value }) => document.querySelector(selector)?.value === value,
    { selector, value },
    { timeout },
  );
}

export async function waitDecisionCount(page, runId, min = 1, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const body = await page.evaluate(async id => {
      const response = await fetch(`/api/decisions?run_id=${encodeURIComponent(id)}`);
      return response.json();
    }, runId);
    if (body.items.length >= min) return;
    await page.waitForTimeout(250);
  }
  throw new Error(`decision count did not reach ${min} for ${runId}`);
}

export async function waitDecision(page, runId, leftRecordId, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const body = await page.evaluate(async id => {
      const response = await fetch(`/api/decisions?run_id=${encodeURIComponent(id)}`);
      return response.json();
    }, runId);
    if (body.items.some(item => item.left === leftRecordId)) return;
    await page.waitForTimeout(250);
  }
  throw new Error(`decision for ${leftRecordId} was not persisted for ${runId}`);
}

// Poll a manual-corpus count on the server (data-entry cycles are count-driven).
export async function waitManualCount(page, expected, timeout = 15000) {
  await page.waitForFunction(
    async expected => {
      const res = await fetch("/api/data-entry");
      const body = await res.json();
      return body.count === expected;
    },
    expected,
    { timeout },
  );
}

// AAA + isolation: every journey runs in a FRESH browser context; STRICT mode
// fails on any page error, console error, or non-2xx /api (403 admin gates
// allowed). Failure artifacts rescued on cleanup.
export async function runJourney(browser, name, fn, options = {}) {
  const started = Date.now();
  E2E_METRICS.journeys++;
  const context = await browser.newContext(options);
  const page = await context.newPage();
  const slug = name.replace(/[^a-z0-9-]+/gi, "-");
  const issues = [];
  const pendingApiRequests = new Set();
  page.on("request", request => {
    if (!request.url().includes("/api/")) return;
    pendingApiRequests.add(request);
    E2E_METRICS.api_requests++;
    const body = request.postDataBuffer();
    if (body) E2E_METRICS.request_bytes += body.length;
  });
  page.on("pageerror", err => issues.push(`pageerror: ${String(err).slice(0, 200)}`));
  page.on("console", msg => {
    if (msg.type() === "error") issues.push(`console: ${msg.text().slice(0, 200)}`);
  });
  page.on("response", res => {
    const pathname = res.url().includes("/api/") ? new URL(res.url()).pathname : "";
    if (!pathname) return;
    pendingApiRequests.delete(res.request());
    E2E_METRICS.api_responses++;
    const contentLength = Number(res.headers()["content-length"] ?? 0);
    if (Number.isFinite(contentLength) && contentLength > 0) {
      E2E_METRICS.response_bytes += contentLength;
    }
    const gated =
      (pathname.startsWith("/api/admin") || pathname.startsWith("/api/data-entry")) &&
      res.status() === 403;
    if (res.status() >= 400 && !gated) E2E_METRICS.api_errors++;
    if (res.status() >= 400 && !gated) {
      issues.push(`api ${res.status()} ${pathname}`);
    }
  });
  page.on("requestfailed", request => {
    if (!request.url().includes("/api/")) return;
    pendingApiRequests.delete(request);
    E2E_METRICS.api_failures++;
    const errorText = request.failure()?.errorText ?? "";
    if (/ERR_ABORTED|aborted|cancelled/i.test(errorText)) {
      E2E_METRICS.api_aborted++;
      return;
    }
    E2E_METRICS.api_errors++;
    issues.push(`api request failed: ${errorText || "unknown"}`);
  });
  const accountPendingRequests = () => {
    for (const request of pendingApiRequests) {
      E2E_METRICS.api_failures++;
      E2E_METRICS.api_aborted++;
      pendingApiRequests.delete(request);
    }
  };
  try {
    await fn(page);
    accountPendingRequests();
    if (issues.length) throw new Error(`strict-mode issues in "${name}": ${issues.join(" | ")}`);
  } catch (err) {
    accountPendingRequests();
    const dir = join(FIXTURE.root, "artifacts");
    mkdirSync(dir, { recursive: true });
    await page.keyboard.press("Escape").catch(() => {});
    await page.screenshot({ path: join(dir, `${slug}.png`), fullPage: true }).catch(() => {});
    writeFileSync(join(dir, `${slug}.html`), await page.content().catch(() => ""));
    throw err;
  } finally {
    E2E_METRICS.journey_ms += Date.now() - started;
    await context.close().catch(() => {});
  }
}

// Data-driven search seed: derive 2 chars from the active bundle (bundle-agnostic).
export async function searchSeed(page) {
  return page.evaluate(async () => {
    const res = await fetch("/api/entities?q=&run_id=&limit=50");
    const rows = (await res.json()).rows;
    const name = rows.find(row => Array.from(String(row.name_kanji ?? "")).some(char => !/\s/u.test(char)) && Array.from(String(row.name_kanji ?? "")).filter(char => !/\s/u.test(char)).length >= 2);
    if (!name) return "N0";
    const characters = Array.from(String(name.name_kanji)).filter(char => !/\s/u.test(char));
    return characters.slice(0, 2).join(" ");
  });
}

const BROWSER_METRIC_COUNTERS = [
  "journeys",
  "journey_ms",
  "api_requests",
  "api_responses",
  "api_failures",
  "api_aborted",
  "api_errors",
  "request_bytes",
  "response_bytes",
];

function writeBrowserMetrics(metricsPath) {
  let previous = {};
  if (existsSync(metricsPath)) {
    try {
      const parsed = JSON.parse(readFileSync(metricsPath, "utf8"));
      if (parsed?.kind === "browser_transport") previous = parsed;
    } catch {}
  }
  const metrics = { schema_version: 1, kind: "browser_transport" };
  for (const key of BROWSER_METRIC_COUNTERS) {
    metrics[key] = Number(previous[key] ?? 0) + E2E_METRICS[key];
  }
  mkdirSync(dirname(metricsPath), { recursive: true });
  const temporary = `${metricsPath}.tmp.${process.pid}`;
  writeFileSync(temporary, JSON.stringify(metrics, null, 2) + "\n");
  renameSync(temporary, metricsPath);
}

// Default: remove the whole fixture; artifacts rescued to a stable path first.
export function cleanupFixture() {
  if (!FIXTURE.root) return;
  const metricsPath = process.env.NAYOSE_E2E_METRICS;
  if (metricsPath) writeBrowserMetrics(metricsPath);
  const artifacts = join(FIXTURE.root, "artifacts");
  if (existsSync(artifacts) && readdirSync(artifacts).length) {
    const kept = join(tmpdir(), `nayose-e2e-artifacts-${Date.now()}`);
    mkdirSync(kept, { recursive: true });
    for (const file of readdirSync(artifacts)) {
      cpSync(join(artifacts, file), join(kept, file), { recursive: true });
    }
    const slog = join(FIXTURE.root, "server.log");
    if (existsSync(slog)) copyFileSync(slog, join(kept, "server.log"));
    console.error(`E2E artifacts saved to ${kept}`);
  }
  if (process.env.NAYOSE_E2E_KEEP) {
    console.error(`fixture kept at ${FIXTURE.root}`);
    return;
  }
  rmSync(FIXTURE.root, { recursive: true, force: true });
}
