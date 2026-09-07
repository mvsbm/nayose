// E2E infrastructure (Playwright): the ONLY entry point to browser automation.
//
// SRP/Facade: journeys must not touch raw Playwright primitives (waitForFunction
// 3-position-args gotcha, reload() memory-cache, context sharing are all sharp
// edges owned HERE). DIP: e2e.test.mjs imports helpers, never "playwright".
// Isolation: every journey gets a FRESH context (clean localStorage/DOM), so a
// failed journey cannot contaminate the next one. The bundle is COPIED to a
// temp root, so repo runtime artifacts are never mutated.
import { spawn } from "node:child_process";
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

// Resolve the "current" target of a models/data root, tolerating a direct dir.
function layoutSource(rootPath) {
  const linked = rootPath && existsSync(join(rootPath, "current"));
  const target = linked ? join(rootPath, "current") : rootPath;
  if (!target || !existsSync(target)) return null;
  const src = realpathSync(target);
  return { src, name: basename(src) };
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
      "E2E needs a trained bundle: set NAYOSE_E2E_MODELS/NAYOSE_E2E_DATA, or train and point models/current + data/current first (scripts/train.py).",
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
  // navigate() sets aria-current on the active nav link for EVERY route — a
  // route-independent boot-complete signal.
  await page.waitForSelector(".side a[aria-current]", { timeout: 15000 });
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

export async function waitHash(page, prefix, timeout = 15000) {
  await page.waitForFunction((p) => location.hash.startsWith(p), prefix, { timeout });
}

export async function waitLang(page, lang, timeout = 15000) {
  await page.waitForFunction((l) => document.documentElement.lang === l, lang, { timeout });
}

export async function waitCount(page, selector, min, timeout = 15000) {
  await page.waitForFunction(
    ({ selector, min }) => document.querySelectorAll(selector).length >= min,
    { selector, min },
    { timeout },
  );
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
  const context = await browser.newContext(options);
  const page = await context.newPage();
  const slug = name.replace(/[^a-z0-9-]+/gi, "-");
  const issues = [];
  page.on("pageerror", err => issues.push(`pageerror: ${String(err).slice(0, 200)}`));
  page.on("console", msg => {
    if (msg.type() === "error") issues.push(`console: ${msg.text().slice(0, 200)}`);
  });
  page.on("response", res => {
    const pathname = res.url().includes("/api/") ? new URL(res.url()).pathname : "";
    if (pathname && res.status() >= 400) {
      const gated = (pathname.startsWith("/api/admin") || pathname.startsWith("/api/data-entry")) && res.status() === 403;
      if (!gated) issues.push(`api ${res.status()} ${pathname}`);
    }
  });
  try {
    await fn(page);
    if (issues.length) throw new Error(`strict-mode issues in "${name}": ${issues.join(" | ")}`);
  } catch (err) {
    const dir = join(FIXTURE.root, "artifacts");
    mkdirSync(dir, { recursive: true });
    await page.keyboard.press("Escape").catch(() => {});
    await page.screenshot({ path: join(dir, `${slug}.png`), fullPage: true }).catch(() => {});
    writeFileSync(join(dir, `${slug}.html`), await page.content().catch(() => ""));
    throw err;
  } finally {
    await context.close().catch(() => {});
  }
}

// Data-driven search seed: derive 2 chars from the active bundle (bundle-agnostic).
export async function searchSeed(page) {
  return page.evaluate(async () => {
    const res = await fetch("/api/entities?q=&run_id=&limit=50");
    const rows = (await res.json()).rows;
    const name = rows.find(row => String(row.name_kanji ?? "").length >= 2);
    return name ? String(name.name_kanji).slice(0, 2) : "N0";
  });
}

// Default: remove the whole fixture; artifacts rescued to a stable path first.
export function cleanupFixture() {
  if (!FIXTURE.root) return;
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
