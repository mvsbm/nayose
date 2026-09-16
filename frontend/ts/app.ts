// Assemble the transport, state store, shell, and route.
// No side effects at import time; tests call boot({ port, store }) with mocks.
import { createPort } from "./infra.js";
import { Store } from "./store.js";
import type { Density, Theme } from "./store.js";
import { applyAppearance } from "./appearance.js";
import { WorkbenchPort } from "./port.js";
import { parseRoute } from "./core.js";
import { RunInfo } from "./shared.js";
import {
  mountCompare,
  mountCompareRuns,
  mountConfusion,
  mountEntities,
  mountEntity,
  mountMetrics,
  mountReview,
  mountSearch,
} from "./features.js";
import { mountAdmin } from "./admin-view.js";
import { mountDataEntry } from "./data-entry-view.js";
import { mountNavigation, renderNavigation } from "./navigation.js";
import { createViewScope, disposable, DisposableStore, type ViewScope } from "./lifecycle.js";
import { routeDescriptor, type RouteName } from "./routes.js";
import { presentError } from "./api-error.js";
import { createDecisionCoordinator } from "./decision-sync.js";
import { mountPalette } from "./palette.js";

export interface BootOptions {
  port?: WorkbenchPort;
  store?: Store;
  runs?: RunInfo[];
  healthIntervalMs?: number; // status-strip poll cadence; 0 disables (tests)
}

type ViewRenderer = (view: HTMLElement, port: WorkbenchPort, store: Store, args: string[], scope: ViewScope) => Promise<void>;
const VIEWS: Partial<Record<RouteName, ViewRenderer>> = {
  search: (v, p, s, _args, scope) => mountSearch(v, p, s, scope),
  review: (v, p, s, _args, scope) => mountReview(v, p, s, scope),
  confusion: (v, p, s, _args, scope) => mountConfusion(v, p, s, scope),
  metrics: (v, p, s, _args, scope) => mountMetrics(v, p, s, scope),
  entities: (v, p, s, _args, scope) => mountEntities(v, p, s, scope),
  entity: (v, p, s, a, scope) => mountEntity(v, p, s, a[0] ?? "", scope),
  compare: (v, p, s, a, scope) => mountCompare(v, p, s, a[0] ?? "", a[1] ?? "", scope),
  "compare-runs": (v, p, s, _args, scope) => mountCompareRuns(v, p, s, scope),
  admin: (v, p, s, _args, scope) => mountAdmin(v, p, s, scope),
  "data-entry": (v, p, s, _args, scope) => mountDataEntry(v, p, s, scope),
};

export async function boot(opts: BootOptions = {}): Promise<{ destroy: () => void }> {
  const store = opts.store ?? new Store();
  const port = opts.port ?? createPort();
  const lifetime = new DisposableStore();
  lifetime.add(disposable(() => store.matching.cancel()));
  const capabilities = { admin: false, dataEntry: false };
  const view = document.querySelector<HTMLElement>("main#view")!;
  let activeView: HTMLElement | undefined;
  let activeViewScope: ViewScope | undefined;
  let navigation = 0;
  const themeKey = (theme: Theme): string => theme === "auto" ? "themeAuto" : theme === "dark" ? "themeDark" : "themeLight";
  const densityKey = (density: Density): string => density === "compact" ? "densityCompact" : "densityComfortable";
  const localizeShell = (): void => {
    document.documentElement.lang = store.locale;
    const currentRoute = routeDescriptor(parseRoute(location.hash).name);
    document.title = `${store.t(currentRoute.titleKey)} — Nayose`;
    document.querySelectorAll<HTMLElement>("[data-i18n]").forEach(el => { el.textContent = store.t(el.dataset.i18n!); });
    document.querySelectorAll<HTMLElement>("[data-i18n-aria]").forEach(el => el.setAttribute("aria-label", store.t(el.dataset.i18nAria!)));
    document.querySelectorAll<HTMLElement>("[data-i18n-placeholder]").forEach(el => el.setAttribute("placeholder", store.t(el.dataset.i18nPlaceholder!)));
    const theme = document.querySelector<HTMLButtonElement>("#theme");
    if (theme) {
      theme.textContent = `${store.t("theme")} · ${store.t(themeKey(store.theme))}`;
      theme.setAttribute("aria-label", `${store.t("theme")} · ${store.t(themeKey(store.theme))}`);
    }
    const density = document.querySelector<HTMLButtonElement>("#density");
    if (density) {
      density.textContent = `${store.t("density")} · ${store.t(densityKey(store.density))}`;
      density.setAttribute("aria-label", `${store.t("density")} · ${store.t(densityKey(store.density))}`);
    }
    const viewTitle = document.querySelector<HTMLElement>("#view-title");
    if (viewTitle) viewTitle.textContent = store.t(currentRoute.titleKey);
    const language = document.querySelector<HTMLButtonElement>("#language")!;
    language.textContent = store.locale === "ja" ? store.t("switchEnglish") : store.t("switchJapanese");
    language.lang = store.locale === "ja" ? "en" : "ja";
    language.setAttribute("aria-label", store.locale === "ja" ? store.t("switchEnglish") : store.t("switchJapanese"));
  };
  const applyChromeAppearance = (): void => {
    applyAppearance(store.theme, store.density);
    localizeShell();
  };
  applyChromeAppearance();
  lifetime.add(disposable(store.on("theme", applyChromeAppearance)));
  lifetime.add(disposable(store.on("density", applyChromeAppearance)));
  const systemTheme = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  const onSystemTheme = (): void => { if (store.theme === "auto") applyChromeAppearance(); };
  if (systemTheme) {
    systemTheme.addEventListener("change", onSystemTheme);
    lifetime.add(disposable(() => systemTheme.removeEventListener("change", onSystemTheme)));
  }
  const primaryNavigation = document.querySelector<HTMLElement>("#primary-navigation")!;
  renderNavigation(primaryNavigation, store);
  const navigationController = mountNavigation(
    document.querySelector<HTMLElement>(".shell")!,
    primaryNavigation,
    document.querySelector<HTMLElement>("#nav-resizer")!,
    document.querySelector<HTMLButtonElement>("#nav-toggle")!,
    document.querySelector<HTMLButtonElement>("#nav-scrim")!,
    store,
  );
  lifetime.add(disposable(navigationController.destroy));

  const paint = async (html: string): Promise<void> => {
    view.innerHTML = `<div class="panel">${html}</div>`;
    view.focus({ preventScroll: true });
  };
  const navigate = async (): Promise<void> => {
    const ticket = ++navigation;
    activeView?.dispatchEvent(new window.Event("unmount"));
    activeViewScope?.dispose();
    const target = document.createElement("div");
    activeView = target;
    activeViewScope = createViewScope(target);
    view.replaceChildren(target);
    if (!store.runs.length) {
      await paint(`<div class="empty"><h2>${esc(store.t("noRun"))}</h2><p>${esc(store.t("noRunHelp"))}</p><button type="button" class="more" id="retry-runs">${esc(store.t("retry"))}</button></div>`);
      target.dataset.viewReady = "true";
      return;
    }
    const route = parseRoute(location.hash);
    const descriptor = routeDescriptor(route.name);
    const navRoute = descriptor.nav;
    document.querySelector<HTMLElement>("#view-title")!.textContent = store.t(descriptor.titleKey);
    document.title = `${store.t(descriptor.titleKey)} — Nayose`;
    document.querySelectorAll<HTMLElement>(".side a[data-nav]").forEach((a) =>
      a.setAttribute("aria-current", a.getAttribute("data-nav") === navRoute ? "page" : ""),
    );
    document.querySelectorAll<HTMLDetailsElement>(".side details[data-nav-group]").forEach((group) => {
      if (group.querySelector(`[data-nav="${navRoute}"]`)) group.open = true;
    });
    window.scrollTo(0, 0);
    const render = VIEWS[route.name];
    try {
      await (render ? render(target, port, store, route.args, activeViewScope!) : mountSearch(target, port, store, activeViewScope!));
      if (!lifetime.isDisposed && ticket === navigation) target.dataset.viewReady = "true";
    } catch (err) {
      if (lifetime.isDisposed || ticket !== navigation) return;
      const message = presentError(err, store.t.bind(store));
      target.innerHTML = `<div class="empty"><p>${esc(store.t("error"))}: ${esc(message)}</p><button class="more" id="retry" type="button">${esc(store.t("retry"))}</button></div>`;
      target.querySelector("#retry")?.addEventListener("click", () => void navigate());
      target.dataset.viewReady = "true";
    }
  };
  const onHashChange = (): void => { void navigate(); };
  window.addEventListener("hashchange", onHashChange);
  lifetime.add(disposable(() => window.removeEventListener("hashchange", onHashChange)));
  const switchLanguage = (): void => store.setLocale(store.locale === "ja" ? "en" : "ja");
  const languageButton = document.querySelector("#language")!;
  languageButton.addEventListener("click", switchLanguage);
  lifetime.add(disposable(() => languageButton.removeEventListener("click", switchLanguage)));
  lifetime.add(disposable(store.on("locale", () => {
    localizeShell();
    if (parseRoute(location.hash).name !== "admin") void navigate();
  })));
  const strip = document.querySelector<HTMLElement>("#status-strip")!;
  const cleanup = (): void => {
    activeView?.dispatchEvent(new window.Event("unmount"));
    activeViewScope?.dispose();
    lifetime.dispose();
    strip.hidden = true;
  };

  // run selector + first-run guidance
  const sel = document.querySelector<HTMLSelectElement>("#run")!;
  const fp = document.querySelector("#run-fp")!;
  if (opts.runs) {
    store.runs = opts.runs;
  } else {
    try {
      store.runs = await port.runs();
    } catch {
      store.runs = [];
    }
  }
  if (store.runs.length === 0) {
    sel.innerHTML = `<option value="">${esc(store.t("none"))}</option>`;
    await navigate();
    document.querySelector<HTMLButtonElement>("#retry-runs")?.addEventListener("click", () => window.location.reload());
    return { destroy: cleanup };
  }
  const current = store.runs.find((r) => r.current) ?? store.runs[0];
  const saved = store.read("nayose.run");
  store.setRun(store.runs.some((r) => r.id === saved) ? saved ?? "" : current?.id ?? "");
  sel.innerHTML = store.runs
    .map((r) => `<option value="${esc(r.id)}" ${r.id === store.run ? "selected" : ""}>${esc(r.id)}${r.current ? " ★" : ""}</option>`)
    .join("");
  const picked = store.runs.find((r) => r.id === store.run);
  fp.textContent = picked?.fingerprint ? `${store.t("fingerprintLabel")} ${esc(picked.fingerprint)}` : "";
  port.run(store.run);
  store.reviewer = store.read("nayose.reviewer") ?? "";
  const decisionCoordinator = createDecisionCoordinator(store, port, lifetime);
  let runChange = 0;
  const onRunChange = (): void => {
    const runId = sel.value;
    const request = ++runChange;
    store.setRun(runId);
    store.write("nayose.run", runId);
    port.run(runId);
    store.clearRunState(false);
    void renderHealth();
    const p = store.runs.find((r) => r.id === runId);
    fp.textContent = p?.fingerprint ? `${store.t("fingerprintLabel")} ${esc(p.fingerprint)}` : "";
    void decisionCoordinator.loadForRun(runId).then(() => {
      if (!lifetime.isDisposed && request === runChange && store.run === runId) void navigate();
    });
  };
  sel.addEventListener("change", onRunChange);
  lifetime.add(disposable(() => sel.removeEventListener("change", onRunChange)));

  // Admin and data entry are local-first surfaces with no separate credential.
  try {
    const cap = await port.adminCapability();
    capabilities.admin = cap.capable;
    capabilities.dataEntry = cap.capable;
    if (cap.capable) {
      document.querySelectorAll("#nav-admin-group, #nav-data-entry").forEach(n => n.removeAttribute("hidden"));
    }
    try { localStorage.removeItem("nayose.adminToken"); } catch { /* storage unavailable */ }
  } catch (error) {
    if (!lifetime.isDisposed) {
      strip.hidden = false;
      strip.innerHTML = `<span class="warn">${esc(store.t("statusUnavailable"))}</span><button type="button" class="more" id="capability-retry">${esc(store.t("retry"))}</button>`;
      strip.querySelector("#capability-retry")?.addEventListener("click", () => window.location.reload());
    }
    console.warn("capability check failed", error);
  }

  await decisionCoordinator.loadForRun(store.run);
  const syncRunSelector = (): void => {
    sel.innerHTML = store.runs.map((r) => `<option value="${esc(r.id)}" ${r.id === store.run ? "selected" : ""}>${esc(r.id)}${r.current ? " ★" : ""}</option>`).join("");
    const selected = store.runs.find((r) => r.id === store.run);
    fp.textContent = selected?.fingerprint ? `${store.t("fingerprintLabel")} ${esc(selected.fingerprint)}` : "";
  };
  const refreshRuns = async (): Promise<void> => {
    try {
      const next = await port.runs();
      if (lifetime.isDisposed || !next.length) return;
      store.runs = next;
      if (!next.some(run => run.id === store.run)) {
        const nextRun = next.find(run => run.current)?.id ?? next[0]!.id;
        store.setRun(nextRun);
        port.run(nextRun);
        store.clearRunState(false);
        await decisionCoordinator.loadForRun(nextRun);
        void renderHealth();
        if (!lifetime.isDisposed) await navigate();
      }
      syncRunSelector();
    } catch {
      /* keep the last known run list */
    }
  };
  const onRunsChanged = (): void => { void refreshRuns(); };
  window.addEventListener("nayose:runs-changed", onRunsChanged);
  lifetime.add(disposable(() => window.removeEventListener("nayose:runs-changed", onRunsChanged)));

  // appearance preferences are state transitions projected by applyChromeAppearance
  const themeButton = document.querySelector("#theme");
  const densityButton = document.querySelector("#density");
  const onThemeClick = (): void => store.toggleTheme();
  const onDensityClick = (): void => store.toggleDensity();
  themeButton?.addEventListener("click", onThemeClick);
  densityButton?.addEventListener("click", onDensityClick);
  if (themeButton) lifetime.add(disposable(() => themeButton.removeEventListener("click", onThemeClick)));
  if (densityButton) lifetime.add(disposable(() => densityButton.removeEventListener("click", onDensityClick)));

  // E — system status strip (visibility of system status): health poll drives it
  let healthController: AbortController | undefined;
  let healthRequest = 0;
  const renderHealth = async (): Promise<void> => {
    healthController?.abort();
    healthController = new AbortController();
    const request = ++healthRequest;
    try {
      const h = await port.health(healthController.signal);
      if (lifetime.isDisposed || request !== healthRequest || !strip.isConnected) return;
      const fpBadge = store.run && h.run && store.run !== h.run
        ? `<span class="bad">${esc(store.t("fpMismatch"))}</span>`
        : `<span class="ok">${esc(store.t("fpMatch"))}</span>`;
      const engine = h.engine_warm
        ? `<span class="ok">${esc(store.t("engineWarm"))}</span>`
        : `<span class="warn">${esc(store.t("engineCold"))}</span>`;
      const stats = (h.stats ?? []).slice(0, 8)
        .map(s => `<tr><td>${esc(s.endpoint)}</td><td>${esc(s.hits)}</td><td>${esc(Math.round(s.total_ms))}ms</td></tr>`)
        .join("");
      strip.hidden = false;
      const sessionRecords = store.checked.size
        ? ` · ${esc(store.t("sessionRecords", { n: store.checked.size }))}`
        : "";
      strip.innerHTML = `<b>${esc(store.t("runningRun"))}:</b> ${esc(h.run ?? "—")} · ${fpBadge} · ${engine}${sessionRecords}
        <details><summary>${esc(store.t("apiStats"))}</summary><table><thead><tr><th scope="col">${esc(store.t("endpoint"))}</th><th scope="col">${esc(store.t("hits"))}</th><th scope="col">${esc(store.t("total"))}</th></tr></thead><tbody>${stats || `<tr><td colspan="3">—</td></tr>`}</tbody></table>
        <p class="members">${esc(store.t("healthNow", { t: (h.now_utc ?? "").slice(0, 19).replace("T", " ") }))}</p></details>`;
    } catch (error) {
      if (lifetime.isDisposed || request !== healthRequest || healthController?.signal.aborted) return;
      strip.hidden = false;
      strip.innerHTML = `<span class="warn">${esc(store.t("statusUnavailable"))}</span><button type="button" class="more" id="health-retry">${esc(store.t("retry"))}</button>`;
      strip.querySelector("#health-retry")?.addEventListener("click", () => void renderHealth());
      console.warn("health check failed", error);
    }
  };
  lifetime.add(disposable(store.on("checked", () => void renderHealth())));
  lifetime.add(disposable(() => healthController?.abort()));
  void renderHealth();
  const healthIntervalMs = opts.healthIntervalMs ?? 30000;
  if (healthIntervalMs > 0) lifetime.addInterval(() => { void renderHealth(); void refreshRuns(); }, healthIntervalMs);

  mountPalette({
    port,
    store,
    lifetime,
    capabilities,
    toggleNavigation: navigationController.toggle,
  });

  await navigate();
  return { destroy: cleanup };
}

const esc = (s: unknown): string =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));

// Real browser: auto-start. Tests import this module with a ?boot=N query
// (to control instantiation) and call boot() themselves — no side effects on import.
if (new URL(import.meta.url).searchParams.get("boot") === null) {
  void boot();
}
