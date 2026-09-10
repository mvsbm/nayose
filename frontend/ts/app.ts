// Assemble the transport, state store, shell, and route.
// No side effects at import time; tests call boot({ port, store }) with mocks.
import { createPort } from "./infra.js";
import { Store } from "./store.js";
import type { Density, Theme } from "./store.js";
import { applyAppearance } from "./appearance.js";
import { WorkbenchPort } from "./port.js";
import { checkedKey, parseRoute } from "./core.js";
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
import { mountNavigation } from "./navigation.js";
import { createViewScope, disposable, DisposableStore, type ViewScope } from "./lifecycle.js";
import { CommandRegistry, createWorkbenchCommands, type CommandContext } from "./commands.js";
import { ROUTES, routeDescriptor, type RouteName } from "./routes.js";

export interface BootOptions {
  port?: WorkbenchPort;
  store?: Store;
  runs?: RunInfo[];
  healthIntervalMs?: number; // status-strip poll cadence; 0 disables (tests)
}

type ViewRenderer = (view: HTMLElement, port: WorkbenchPort, store: Store, args: string[], scope: ViewScope) => Promise<void>;
const VIEWS: Partial<Record<RouteName, ViewRenderer>> = {
  search: (v, p, s, _args, scope) => mountSearch(v, p, s, scope),
  review: (v, p, s) => mountReview(v, p, s),
  confusion: (v, p, s) => mountConfusion(v, p, s),
  metrics: (v, p, s) => mountMetrics(v, p, s),
  entities: (v, p, s, _args, scope) => mountEntities(v, p, s, scope),
  entity: (v, p, s, a, scope) => mountEntity(v, p, s, a[0] ?? "", scope),
  compare: (v, p, s, a) => mountCompare(v, p, s, a[0] ?? "", a[1] ?? ""),
  "compare-runs": (v, p, s) => mountCompareRuns(v, p, s),
  admin: (v, p, s, _args, scope) => mountAdmin(v, p, s, scope),
  "data-entry": (v, p, s, _args, scope) => mountDataEntry(v, p, s, scope),
};

export async function boot(opts: BootOptions = {}): Promise<{ destroy: () => void }> {
  const store = opts.store ?? new Store();
  const port = opts.port ?? createPort();
  const lifetime = new DisposableStore();
  const capabilities = { admin: false, dataEntry: false };
  const view = document.querySelector<HTMLElement>("main#view")!;
  let activeView: HTMLElement | undefined;
  let activeViewScope: ViewScope | undefined;
  let navigation = 0;
  const themeKey = (theme: Theme): string => theme === "auto" ? "themeAuto" : theme === "dark" ? "themeDark" : "themeLight";
  const densityKey = (density: Density): string => density === "compact" ? "densityCompact" : "densityComfortable";
  const localizeShell = (): void => {
    document.documentElement.lang = store.locale;
    document.title = `Nayose — ${store.t("title")}`;
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
    const language = document.querySelector<HTMLButtonElement>("#language")!;
    language.textContent = store.locale === "ja" ? "English" : "日本語";
    language.lang = store.locale === "ja" ? "en" : "ja";
    language.setAttribute("aria-label", store.locale === "ja" ? "Switch to English" : "日本語に切り替え");
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
  const navigationController = mountNavigation(
    document.querySelector<HTMLElement>(".shell")!,
    document.querySelector<HTMLElement>("#activity-navigation")!,
    document.querySelector<HTMLElement>("#primary-navigation")!,
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
      await paint(`<div class="empty"><h2>${esc(store.t("noRun"))}</h2><p>${esc(store.t("noRunHelp"))}</p></div>`);
      return;
    }
    const route = parseRoute(location.hash);
    const navRoute = routeDescriptor(route.name).nav;
    document.querySelectorAll<HTMLElement>(".side a[data-nav]").forEach((a) =>
      a.setAttribute("aria-current", a.getAttribute("data-nav") === navRoute ? "page" : ""),
    );
    document.querySelectorAll<HTMLElement>(".activity-bar a[data-activity]").forEach((a) => {
      const routes = (a.dataset.activityRoutes ?? "").split(",");
      a.setAttribute("aria-current", routes.includes(navRoute) ? "page" : "");
    });
    document.querySelectorAll<HTMLDetailsElement>(".side details[data-nav-group]").forEach((group) => {
      if (group.querySelector(`[data-nav="${navRoute}"]`)) group.open = true;
    });
    window.scrollTo(0, 0);
    const render = VIEWS[route.name];
    try {
      await (render ? render(target, port, store, route.args, activeViewScope!) : mountSearch(target, port, store, activeViewScope!));
    } catch (err) {
      if (lifetime.isDisposed || ticket !== navigation) return;
      target.innerHTML = `<div class="empty"><p>${esc(store.t("error"))}: ${esc(store.t(String((err as Error).message ?? err)))}</p><button class="more" id="retry">${esc(store.t("retry"))}</button></div>`;
      target.querySelector("#retry")?.addEventListener("click", () => void navigate());
    }
  };
  const onHashChange = (): void => { void navigate(); };
  window.addEventListener("hashchange", onHashChange);
  lifetime.add(disposable(() => window.removeEventListener("hashchange", onHashChange)));
  const switchLanguage = (): void => store.setLocale(store.locale === "ja" ? "en" : "ja");
  const languageButton = document.querySelector("#language")!;
  languageButton.addEventListener("click", switchLanguage);
  lifetime.add(disposable(() => languageButton.removeEventListener("click", switchLanguage)));
  lifetime.add(disposable(store.on("locale", () => { localizeShell(); void navigate(); })));
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
    return { destroy: cleanup };
  }
  const current = store.runs.find((r) => r.current) ?? store.runs[0];
  const saved = store.read("nayose.run");
  store.setRun(store.runs.some((r) => r.id === saved) ? saved ?? "" : current?.id ?? "");
  sel.innerHTML = store.runs
    .map((r) => `<option value="${esc(r.id)}" ${r.id === store.run ? "selected" : ""}>${esc(r.id)}${r.current ? " ★" : ""}</option>`)
    .join("");
  const picked = store.runs.find((r) => r.id === store.run);
  fp.textContent = picked?.fingerprint ? `fingerprint ${esc(picked.fingerprint)}` : "";
  port.run(store.run);
  const onRunChange = (): void => {
    store.setRun(sel.value);
    store.write("nayose.run", store.run);
    port.run(store.run);
    store.clearRunState();
    const p = store.runs.find((r) => r.id === store.run);
    fp.textContent = p?.fingerprint ? `fingerprint ${esc(p.fingerprint)}` : "";
    void navigate();
  };
  sel.addEventListener("change", onRunChange);
  lifetime.add(disposable(() => sel.removeEventListener("change", onRunChange)));

  // Admin and data entry are local-first surfaces with no separate credential.
  try {
    const cap = await port.adminCapability().catch(() => ({ capable: false }));
    capabilities.admin = cap.capable;
    capabilities.dataEntry = cap.capable;
    if (cap.capable) {
      document.querySelectorAll("#nav-admin-group, #nav-data-entry, #activity-admin").forEach(n => n.removeAttribute("hidden"));
    }
    try { localStorage.removeItem("nayose.adminToken"); } catch { /* storage unavailable */ }
  } catch { /* server unavailable: keep the nav hidden */ }

  // decision persistence: localStorage (offline) + server-side (survives machines)
  const storedChecked = store.read("nayose.checked");
  try {
    const parsed = JSON.parse(storedChecked ?? "[]") as unknown;
    store.checked = new Set(Array.isArray(parsed) ? parsed.filter((key): key is string => typeof key === "string") : []);
  } catch {
    store.checked = new Set();
  }
  store.reviewer = store.read("nayose.reviewer") ?? "";
  const persistChecked = (): void => store.write("nayose.checked", JSON.stringify([...store.checked]));
  try {
    const remote = await port.loadDecisions();
    if (remote.length) {
      store.checked = new Set(remote.map((d) => checkedKey(store.run, d.left, d.right)));
    }
  } catch {
    /* server unreachable -> keep localStorage state */
  }
  let decisionSave: Promise<void> = Promise.resolve();
  let decisionTimer: number | undefined;
  const persistDecisions = (): void => {
    persistChecked();
    window.clearTimeout(decisionTimer);
    decisionTimer = lifetime.addTimeout(() => {
      const now = new Date().toISOString();
      const items = [...store.checked].map((key) => {
        const parts = key.split("|");
        return { left: parts[1] ?? "", right: parts[2] ?? "", ts: now, reviewer: store.reviewer };
      });
      decisionSave = decisionSave
        .catch(() => {})
        .then(() => port.saveDecisions(items))
        .catch(() => {});
    }, 500);
  };
  lifetime.add(disposable(store.on("checked", persistDecisions)));

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
  const renderHealth = async (): Promise<void> => {
    try {
      const h = await port.health();
      if (lifetime.isDisposed || !strip.isConnected) return;
      const fpBadge = store.run && h.run && store.run !== h.run
        ? `<span class="bad">${esc(store.t("fpMismatch"))}</span>`
        : `<span class="ok">${esc(store.t("fpMatch"))}</span>`;
      const engine = h.engine_warm
        ? `<span class="ok">${esc(store.t("engineWarm"))}</span>`
        : `<span class="warn">${esc(store.t("engineCold"))}</span>`;
      const stats = (h.stats ?? []).slice(0, 8)
        .map(s => `<tr><td>${esc(s.endpoint)}</td><td>${s.hits}</td><td>${Math.round(s.total_ms)}ms</td></tr>`)
        .join("");
      strip.hidden = false;
      const sessionRecords = store.checked.size
        ? ` · ${esc(store.t("sessionRecords", { n: store.checked.size }))}`
        : "";
      strip.innerHTML = `<b>${esc(store.t("runningRun"))}:</b> ${esc(h.run ?? "—")} · ${fpBadge} · ${engine}${sessionRecords}
        <details><summary>${esc(store.t("apiStats"))}</summary><table><thead><tr><th>endpoint</th><th>hits</th><th>total</th></tr></thead><tbody>${stats || `<tr><td colspan="3">—</td></tr>`}</tbody></table>
        <p class="members">${esc(store.t("healthNow", { t: (h.now_utc ?? "").slice(0, 19).replace("T", " ") }))}</p></details>`;
    } catch { /* server unreachable: keep the strip silent */ }
  };
  lifetime.add(disposable(store.on("checked", () => void renderHealth())));
  void renderHealth();
  const healthIntervalMs = opts.healthIntervalMs ?? 30000;
  if (healthIntervalMs > 0) {
    const healthTimer = window.setInterval(() => void renderHealth(), healthIntervalMs);
    lifetime.add(disposable(() => window.clearInterval(healthTimer)));
  }

  const commandRegistry = new CommandRegistry();
  lifetime.add(commandRegistry.registerAll(createWorkbenchCommands(ROUTES)));

  // ⌘K palette (keyboard-first) — powered by the typed command/query ports
  const palette = document.querySelector<HTMLElement>("#palette")!;
  const pInput = document.querySelector<HTMLInputElement>("#palette-input")!;
  const pList = document.querySelector<HTMLUListElement>("#palette-results")!;
  const pStat = document.querySelector("#palette-stat")!;
  type PaletteItem = { label: string; meta: string; go: () => void };
  let pItems: PaletteItem[] = [];
  let pActive = -1;
  let paletteRequest = 0;
  let focusBeforePalette: HTMLElement | null = null;
  const openPalette = (): void => {
    const active = document.activeElement as HTMLElement | null;
    const opener = document.querySelector<HTMLElement>("#cmd");
    focusBeforePalette = active && active !== document.body && active !== document.documentElement && typeof active.focus === "function" ? active : opener;
    palette.classList.add("open");
    palette.hidden = false;
    pInput.value = "";
    pStat.textContent = "";
    void paletteSearch("");
    pInput.focus();
  };
  const closePalette = (): void => {
    paletteRequest += 1;
    palette.classList.remove("open");
    palette.hidden = true;
    pActive = -1;
    const restore = focusBeforePalette;
    focusBeforePalette = null;
    const target = restore?.isConnected ? restore : document.querySelector<HTMLElement>("#cmd");
    target?.focus();
  };
  const commandContext = (): CommandContext => ({
    t: (key) => store.t(key),
    closePalette,
    navigate: (hash) => { location.hash = hash; },
    toggleNavigation: () => navigationController.toggle(),
    setTheme: (theme) => store.setTheme(theme),
    setDensity: (density) => store.setDensity(density),
    announce: (message) => { pStat.textContent = message; },
    capabilities,
  });
  async function paletteSearch(q: string): Promise<void> {
    const request = ++paletteRequest;
    const query = q.trim().toLocaleLowerCase();
    const items: PaletteItem[] = commandRegistry.list(commandContext(), query).map((command) => ({
      label: command.label,
      meta: command.meta,
      go: () => commandRegistry.execute(command.id, commandContext()),
    }));
    if (q.length >= 2) {
      pStat.textContent = store.t("searching");
      try {
        const res = await port.entities(q);
        if (request !== paletteRequest) return;
        res.rows.forEach((h) => items.push({ label: `${h.name_kanji} ${h.name_kana ?? ""}`, meta: h.nayose_id, go: () => { closePalette(); location.hash = `#/entity/${h.nayose_id}`; } }));
        pStat.textContent = store.t("count", { n: res.rows.length });
      } catch (err) {
        if (request !== paletteRequest) return;
        pStat.textContent = store.t("error") + ": " + String((err as Error)?.message ?? err);
      }
    }
    if (request !== paletteRequest) return;
    pItems = items.slice(0, 14);
    pActive = pItems.length ? 0 : -1;
    renderPaletteList();
  }
  const renderPaletteList = (): void => {
    pList.innerHTML = pItems
      .map((it, i) => `<li role="option" id="p-${i}" aria-selected="${i === pActive}" data-i="${i}"><span>${esc(it.label)}</span><span class="muted">${esc(it.meta)}</span></li>`)
      .join("");
    pList.setAttribute("aria-activedescendant", pActive >= 0 ? `p-${pActive}` : "");
    pList.querySelector(`[data-i="${pActive}"]`)?.scrollIntoView({ block: "nearest" });
  };
  const onPaletteInput = (): void => void paletteSearch(pInput.value);
  const onPaletteKeydown = (e: KeyboardEvent): void => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (pItems.length) { pActive = (pActive + (e.key === "ArrowDown" ? 1 : pItems.length - 1)) % pItems.length; renderPaletteList(); }
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (pActive >= 0 && pItems[pActive]) pItems[pActive]!.go();
    } else if (e.key === "Escape") { closePalette(); }
  };
  const onPaletteListClick = (e: MouseEvent): void => {
    const li = (e.target as HTMLElement).closest<HTMLElement>("li");
    const i = Number(li?.dataset.i);
    if (pItems[i]) pItems[i]!.go();
  };
  pInput.addEventListener("input", onPaletteInput);
  pInput.addEventListener("keydown", onPaletteKeydown);
  pList.addEventListener("click", onPaletteListClick);
  lifetime.add(disposable(() => pInput.removeEventListener("input", onPaletteInput)));
  lifetime.add(disposable(() => pInput.removeEventListener("keydown", onPaletteKeydown)));
  lifetime.add(disposable(() => pList.removeEventListener("click", onPaletteListClick)));
  const paletteKey = (e: KeyboardEvent): void => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      openPalette();
    } else if (!palette.hidden && e.key === "Tab") {
      e.preventDefault();
      pInput.focus();
    } else if (!palette.hidden && e.key === "Escape") {
      closePalette();
    }
  };
  const commandButton = document.querySelector("#cmd");
  const onPaletteBackdrop = (e: MouseEvent): void => { if (e.target === palette) closePalette(); };
  document.addEventListener("keydown", paletteKey);
  commandButton?.addEventListener("click", openPalette);
  palette.addEventListener("click", onPaletteBackdrop);
  lifetime.add(disposable(() => document.removeEventListener("keydown", paletteKey)));
  if (commandButton) lifetime.add(disposable(() => commandButton.removeEventListener("click", openPalette)));
  lifetime.add(disposable(() => palette.removeEventListener("click", onPaletteBackdrop)));

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
