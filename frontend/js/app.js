// Assemble the transport, state store, shell, and route.
// No side effects at import time; tests call boot({ port, store }) with mocks.
import { createPort } from "./infra.js";
import { Store } from "./store.js";
import { applyAppearance } from "./appearance.js";
import { parseRoute } from "./core.js";
import { mountCompare, mountCompareRuns, mountConfusion, mountEntities, mountEntity, mountMetrics, mountReview, mountSearch, } from "./features.js";
import { mountAdmin } from "./admin-view.js";
import { mountDataEntry } from "./data-entry-view.js";
import { mountActivityBar, mountNavigation, renderActivityBar, renderNavigation, setActiveSidebarGroup, renderWorkbenchTabs, updateWorkbenchSelection } from "./navigation.js";
import { createViewScope, disposable, DisposableStore } from "./lifecycle.js";
import { routeDescriptor } from "./routes.js";
import { presentError } from "./api-error.js";
import { createDecisionCoordinator } from "./decision-sync.js";
import { mountPalette } from "./palette.js";
import { observeTables } from "./table-controls.js";
import { formatBytes, formatPercent, formatTemperature, renderResourceStatus } from "./health-view.js";
import { esc } from "./schema.js";
import { markFrontend, measureFrontend } from "./performance.js";
import { defineComponents } from "./components/register.js";
const VIEWS = {
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
export async function boot(opts = {}) {
    markFrontend("boot-start");
    defineComponents();
    const store = opts.store ?? new Store();
    const port = opts.port ?? createPort();
    const lifetime = new DisposableStore();
    lifetime.add(disposable(() => store.matching.cancel()));
    const capabilities = { admin: false, dataEntry: false };
    const view = document.querySelector("main#view");
    const statusBar = document.querySelector("#workbench-status");
    statusBar.innerHTML = `<span class="status-bar-item">● ${esc(store.t("ready"))}</span>`;
    let activeView;
    let activeViewScope;
    let tableObserver;
    let navigation = 0;
    const themeKey = (theme) => theme === "auto" ? "themeAuto" : theme === "dark" ? "themeDark" : "themeLight";
    const densityKey = (density) => density === "compact" ? "densityCompact" : "densityComfortable";
    const localizeShell = () => {
        document.documentElement.lang = store.locale;
        const currentRoute = routeDescriptor(parseRoute(location.hash).name);
        document.title = `${store.t(currentRoute.titleKey)} — Nayose`;
        document.querySelectorAll("[data-i18n]").forEach(el => { el.textContent = store.t(el.dataset.i18n); });
        document.querySelectorAll("[data-i18n-aria]").forEach(el => el.setAttribute("aria-label", store.t(el.dataset.i18nAria)));
        document.querySelectorAll("[data-i18n-title]").forEach(el => { el.title = store.t(el.dataset.i18nTitle); });
        document.querySelectorAll(".activity-item").forEach(el => { el.title = el.getAttribute("aria-label") ?? ""; });
        document.querySelectorAll("[data-i18n-placeholder]").forEach(el => el.setAttribute("placeholder", store.t(el.dataset.i18nPlaceholder)));
        const theme = document.querySelector("#theme");
        if (theme) {
            theme.textContent = `${store.t("theme")} · ${store.t(themeKey(store.theme))}`;
            theme.setAttribute("aria-label", `${store.t("theme")} · ${store.t(themeKey(store.theme))}`);
        }
        const density = document.querySelector("#density");
        if (density) {
            density.textContent = `${store.t("density")} · ${store.t(densityKey(store.density))}`;
            density.setAttribute("aria-label", `${store.t("density")} · ${store.t(densityKey(store.density))}`);
        }
        const viewTitle = document.querySelector("#view-title");
        if (viewTitle)
            viewTitle.textContent = store.t(currentRoute.titleKey);
        const language = document.querySelector("#language");
        language.textContent = store.locale === "ja" ? store.t("switchEnglish") : store.t("switchJapanese");
        language.lang = store.locale === "ja" ? "en" : "ja";
        language.setAttribute("aria-label", store.locale === "ja" ? store.t("switchEnglish") : store.t("switchJapanese"));
    };
    const applyChromeAppearance = () => {
        applyAppearance(store.theme, store.density);
        localizeShell();
    };
    applyChromeAppearance();
    lifetime.add(disposable(store.on("theme", applyChromeAppearance)));
    lifetime.add(disposable(store.on("density", applyChromeAppearance)));
    const systemTheme = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
    const onSystemTheme = () => { if (store.theme === "auto")
        applyChromeAppearance(); };
    if (systemTheme) {
        systemTheme.addEventListener("change", onSystemTheme);
        lifetime.add(disposable(() => systemTheme.removeEventListener("change", onSystemTheme)));
    }
    const primaryNavigation = document.querySelector("#primary-navigation");
    const activityBar = document.querySelector("#activity-bar");
    const workbenchTabs = document.querySelector("#workbench-tabs");
    const initialRoute = parseRoute(location.hash);
    renderActivityBar(activityBar, store);
    renderNavigation(primaryNavigation, store, routeDescriptor(initialRoute.name).group);
    renderWorkbenchTabs(workbenchTabs, store, initialRoute.name);
    const navigationController = mountNavigation(document.querySelector(".shell"), primaryNavigation, document.querySelector("#nav-resizer"), document.querySelector("#nav-toggle"), document.querySelector("#nav-scrim"), store);
    lifetime.add(disposable(navigationController.destroy));
    const activityController = mountActivityBar(activityBar, store, navigationController.toggle);
    lifetime.add(disposable(activityController.destroy));
    const paint = async (html) => {
        view.innerHTML = `<div class="panel">${html}</div>`;
        view.focus({ preventScroll: true });
    };
    const navigate = async () => {
        const ticket = ++navigation;
        const routeStart = `route-start-${ticket}`;
        const routeReady = `route-ready-${ticket}`;
        markFrontend(routeStart);
        activeView?.dispatchEvent(new window.Event("unmount"));
        activeViewScope?.dispose();
        tableObserver?.dispose();
        const target = document.createElement("div");
        activeView = target;
        tableObserver = observeTables(target, store);
        activeViewScope = createViewScope(target);
        view.replaceChildren(target);
        if (!store.runs.length) {
            await paint(`<div class="empty"><h2>${esc(store.t("noRun"))}</h2><p>${esc(store.t("noRunHelp"))}</p><button type="button" class="button button-secondary" id="retry-runs">${esc(store.t("retry"))}</button></div>`);
            target.dataset.viewReady = "true";
            markFrontend(routeReady);
            measureFrontend(routeReady, routeStart, routeReady);
            return;
        }
        const route = parseRoute(location.hash);
        const descriptor = routeDescriptor(route.name);
        const navRoute = descriptor.nav;
        document.querySelector("#view-title").textContent = store.t(descriptor.titleKey);
        document.title = `${store.t(descriptor.titleKey)} — Nayose`;
        renderWorkbenchTabs(workbenchTabs, store, route.name);
        updateWorkbenchSelection(activityBar, workbenchTabs, route.name);
        setActiveSidebarGroup(primaryNavigation, descriptor.group);
        document.querySelectorAll(".side a[data-nav]").forEach((a) => a.setAttribute("aria-current", a.getAttribute("data-nav") === navRoute ? "page" : ""));
        document.querySelectorAll(".side details[data-nav-group]").forEach((group) => {
            if (group.querySelector(`[data-nav="${navRoute}"]`))
                group.open = true;
        });
        window.scrollTo(0, 0);
        const render = VIEWS[route.name];
        try {
            await (render ? render(target, port, store, route.args, activeViewScope) : mountSearch(target, port, store, activeViewScope));
            if (!lifetime.isDisposed && ticket === navigation) {
                target.dataset.viewReady = "true";
                markFrontend(routeReady);
                measureFrontend(routeReady, routeStart, routeReady);
            }
        }
        catch (err) {
            if (lifetime.isDisposed || ticket !== navigation)
                return;
            const message = presentError(err, store.t.bind(store));
            target.innerHTML = `<div class="empty"><p>${esc(store.t("error"))}: ${esc(message)}</p><button class="button button-secondary" id="retry" type="button">${esc(store.t("retry"))}</button></div>`;
            target.querySelector("#retry")?.addEventListener("click", () => void navigate());
            target.dataset.viewReady = "true";
            markFrontend(routeReady);
            measureFrontend(routeReady, routeStart, routeReady);
        }
    };
    const onHashChange = () => { void navigate(); };
    window.addEventListener("hashchange", onHashChange);
    lifetime.add(disposable(() => window.removeEventListener("hashchange", onHashChange)));
    const switchLanguage = () => store.setLocale(store.locale === "ja" ? "en" : "ja");
    const languageButton = document.querySelector("#language");
    languageButton.addEventListener("click", switchLanguage);
    lifetime.add(disposable(() => languageButton.removeEventListener("click", switchLanguage)));
    lifetime.add(disposable(store.on("locale", () => {
        localizeShell();
        void renderHealth();
        if (parseRoute(location.hash).name !== "admin")
            void navigate();
    })));
    const strip = document.querySelector("#status-strip");
    lifetime.add(disposable(() => tableObserver?.dispose()));
    lifetime.add(observeTables(strip, store));
    const cleanup = () => {
        activeView?.dispatchEvent(new window.Event("unmount"));
        activeViewScope?.dispose();
        lifetime.dispose();
        strip.hidden = true;
    };
    // run selector + first-run guidance
    const sel = document.querySelector("#run");
    const fp = document.querySelector("#run-fp");
    if (opts.runs) {
        store.runs = opts.runs;
    }
    else {
        try {
            store.runs = await port.runs();
        }
        catch {
            store.runs = [];
        }
    }
    if (store.runs.length === 0) {
        sel.innerHTML = `<option value="">${esc(store.t("none"))}</option>`;
        await navigate();
        document.querySelector("#retry-runs")?.addEventListener("click", () => window.location.reload());
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
    statusBar.innerHTML = `<span class="status-bar-item">● ${esc(store.t("ready"))}</span><span class="status-bar-item">${esc(store.run)}</span>`;
    store.reviewer = store.read("nayose.reviewer") ?? "";
    const decisionCoordinator = createDecisionCoordinator(store, port, lifetime);
    let runChange = 0;
    const onRunChange = () => {
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
            if (!lifetime.isDisposed && request === runChange && store.run === runId)
                void navigate();
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
            document.querySelectorAll("#nav-admin-group, #nav-data-entry, #activity-admin").forEach(n => n.removeAttribute("hidden"));
            setActiveSidebarGroup(primaryNavigation, routeDescriptor(parseRoute(location.hash).name).group);
        }
        try {
            localStorage.removeItem("nayose.adminToken");
        }
        catch { /* storage unavailable */ }
    }
    catch (error) {
        if (!lifetime.isDisposed) {
            strip.hidden = false;
            strip.innerHTML = `<span class="warn">${esc(store.t("statusUnavailable"))}</span><button type="button" class="button button-secondary" id="capability-retry">${esc(store.t("retry"))}</button>`;
            strip.querySelector("#capability-retry")?.addEventListener("click", () => window.location.reload());
        }
        console.warn("capability check failed", error);
    }
    await decisionCoordinator.loadForRun(store.run);
    const syncRunSelector = () => {
        sel.innerHTML = store.runs.map((r) => `<option value="${esc(r.id)}" ${r.id === store.run ? "selected" : ""}>${esc(r.id)}${r.current ? " ★" : ""}</option>`).join("");
        const selected = store.runs.find((r) => r.id === store.run);
        fp.textContent = selected?.fingerprint ? `${store.t("fingerprintLabel")} ${esc(selected.fingerprint)}` : "";
    };
    const refreshRuns = async () => {
        try {
            const next = await port.runs();
            if (lifetime.isDisposed || !next.length)
                return;
            store.runs = next;
            if (!next.some(run => run.id === store.run)) {
                const nextRun = next.find(run => run.current)?.id ?? next[0].id;
                store.setRun(nextRun);
                port.run(nextRun);
                store.clearRunState(false);
                await decisionCoordinator.loadForRun(nextRun);
                void renderHealth();
                if (!lifetime.isDisposed)
                    await navigate();
            }
            syncRunSelector();
        }
        catch {
            /* keep the last known run list */
        }
    };
    const onRunsChanged = () => { void refreshRuns(); };
    window.addEventListener("nayose:runs-changed", onRunsChanged);
    lifetime.add(disposable(() => window.removeEventListener("nayose:runs-changed", onRunsChanged)));
    // appearance preferences are state transitions projected by applyChromeAppearance
    const themeButton = document.querySelector("#theme");
    const densityButton = document.querySelector("#density");
    const onThemeClick = () => store.toggleTheme();
    const onDensityClick = () => store.toggleDensity();
    themeButton?.addEventListener("click", onThemeClick);
    densityButton?.addEventListener("click", onDensityClick);
    if (themeButton)
        lifetime.add(disposable(() => themeButton.removeEventListener("click", onThemeClick)));
    if (densityButton)
        lifetime.add(disposable(() => densityButton.removeEventListener("click", onDensityClick)));
    // E — system status strip (visibility of system status): health poll drives it
    let healthController;
    let healthTimer;
    let healthRequest = 0;
    const scheduleHealthPoll = (delayMs) => {
        if (healthTimer !== undefined)
            lifetime.clearTimeout(healthTimer);
        if (delayMs <= 0)
            return;
        healthTimer = lifetime.addTimeout(() => {
            healthTimer = undefined;
            void renderHealth();
        }, delayMs);
    };
    const renderHealth = async () => {
        if (healthTimer !== undefined) {
            lifetime.clearTimeout(healthTimer);
            healthTimer = undefined;
        }
        healthController?.abort();
        healthController = new AbortController();
        const request = ++healthRequest;
        try {
            const h = await port.health(healthController.signal);
            if (lifetime.isDisposed || request !== healthRequest || !strip.isConnected)
                return;
            const fpBadge = store.run && h.run && store.run !== h.run
                ? `<span class="bad">${esc(store.t("fpMismatch"))}</span>`
                : `<span class="ok">${esc(store.t("fpMatch"))}</span>`;
            const engine = h.engine_warm
                ? `<span class="ok">${esc(store.t("engineWarm"))}</span>`
                : `<span class="warn">${esc(store.t("engineCold"))}</span>`;
            const stats = (h.stats ?? []).slice(0, 8)
                .map(s => `<tr><td>${esc(s.endpoint)}</td><td>${esc(s.hits)}</td><td>${esc(Math.round(s.total_ms))}ms</td></tr>`)
                .join("");
            const resources = h.resources;
            const memory = resources?.memory;
            const storage = resources?.storage;
            const policy = resources?.policy;
            const telemetry = resources?.telemetry;
            const gpuAccelerator = resources?.accelerators?.find(accelerator => accelerator.kind === "cuda" || accelerator.kind === "rocm");
            const gpuIsUnified = policy?.device === "mps" || policy?.device === "mlx_gpu";
            const gpuMemoryLabel = gpuIsUnified ? store.t("sharedMemory") : store.t("vramAvailable");
            const gpuMemoryBytes = telemetry?.gpu_memory_available_bytes ?? (gpuIsUnified ? memory?.available_bytes : gpuAccelerator?.available_memory_bytes);
            const gpuMemory = gpuMemoryBytes == null ? "—" : formatBytes(gpuMemoryBytes);
            const telemetryDetails = [
                telemetry?.cpu_usage_percent != null ? `<div><dt>${esc(store.t("cpuUsage"))}</dt><dd>${esc(formatPercent(telemetry.cpu_usage_percent))}</dd></div>` : "",
                telemetry?.cpu_temperature_celsius != null ? `<div><dt>${esc(store.t("temperature"))}</dt><dd>${esc(formatTemperature(telemetry.cpu_temperature_celsius))}</dd></div>` : "",
                telemetry?.gpu_usage_percent != null ? `<div><dt>${esc(store.t("gpuUsage"))}</dt><dd>${esc(formatPercent(telemetry.gpu_usage_percent))}</dd></div>` : "",
                telemetry?.gpu_temperature_celsius != null ? `<div><dt>${esc(store.t("temperature"))}</dt><dd>${esc(formatTemperature(telemetry.gpu_temperature_celsius))}</dd></div>` : "",
                gpuMemoryBytes != null ? `<div><dt>${esc(gpuMemoryLabel)}</dt><dd>${esc(gpuMemory)}</dd></div>` : "",
            ].filter(Boolean).join("");
            const resourceSummary = resources
                ? ` · ${esc(resources.machine)} · ${esc(resources.available_cpus)}/${esc(resources.cpu_count)} ${esc(store.t("cpu"))} · ${esc(policy?.process_workers ?? "—")} ${esc(store.t("workers"))} · ${esc(formatBytes(memory?.available_bytes ?? 0))} ${esc(store.t("ramAvailable"))} · ${esc(formatBytes(storage?.available_bytes ?? 0))} ${esc(store.t("storageAvailable"))}`
                : "";
            const resourcePanel = resources
                ? `<details><summary>${esc(store.t("resourceDetails"))}</summary><dl class="configuration-values">
          <div><dt>${esc(store.t("host"))}</dt><dd>${esc(resources.operating_system)}</dd></div>
          <div><dt>${esc(store.t("architecture"))}</dt><dd>${esc(resources.machine)}</dd></div>
          <div><dt>${esc(store.t("cpu"))}</dt><dd>${esc(resources.available_cpus)} / ${esc(resources.cpu_count)}</dd></div>
          ${telemetryDetails}
          <div><dt>${esc(store.t("workers"))}</dt><dd>${esc(policy?.process_workers ?? "—")}</dd></div>
          <div><dt>${esc(store.t("configuredDevice"))}</dt><dd>${esc(policy?.device ?? "—")}</dd></div>
          <div><dt>${esc(store.t("batchSize"))}</dt><dd>${esc(policy?.embedding_batch_size ?? "—")}</dd></div>
          <div><dt>${esc(store.t("ramAvailable"))}</dt><dd>${esc(formatBytes(memory?.available_bytes ?? 0))} / ${esc(formatBytes(memory?.total_bytes ?? 0))}</dd></div>
          <div><dt>${esc(store.t("storageAvailable"))}</dt><dd>${esc(formatBytes(storage?.available_bytes ?? 0))} / ${esc(formatBytes(storage?.total_bytes ?? 0))}</dd></div>
          <div><dt>${esc(store.t("configurationSource"))}</dt><dd>${esc(h.configuration?.source ?? "—")}</dd></div>
          <div><dt>${esc(store.t("configurationVersion"))}</dt><dd>${esc(h.configuration?.schema_version ?? "—")}</dd></div>
          <div><dt>${esc(store.t("configurationHash"))}</dt><dd>${esc(h.configuration?.hash?.slice(0, 12) ?? "—")}</dd></div>
        </dl></details>`
                : "";
            const lowStorage = storage && storage.reserve_bytes !== undefined && storage.available_bytes <= storage.reserve_bytes
                ? `<span class="bad">${esc(store.t("lowStorage"))}</span>`
                : "";
            strip.hidden = false;
            statusBar.innerHTML = `<span class="status-bar-item">● ${esc(store.t("ready"))}</span><span class="status-bar-item">${esc(h.run ?? store.run)}</span><span class="status-bar-item">${h.engine_warm ? esc(store.t("engineWarm")) : esc(store.t("engineCold"))}</span>${renderResourceStatus(h.resources, store)}`;
            const sessionRecords = store.checked.size
                ? ` · ${esc(store.t("sessionRecords", { n: store.checked.size }))}`
                : "";
            strip.innerHTML = `<b>${esc(store.t("runningRun"))}:</b> ${esc(h.run ?? "—")} · ${fpBadge} · ${engine}${resourceSummary}${sessionRecords} ${lowStorage}
        ${resourcePanel}
        <details><summary>${esc(store.t("apiStats"))}</summary><table><thead><tr><th scope="col">${esc(store.t("endpoint"))}</th><th scope="col">${esc(store.t("hits"))}</th><th scope="col">${esc(store.t("total"))}</th></tr></thead><tbody>${stats || `<tr><td colspan="3">—</td></tr>`}</tbody></table>
        <p class="text-meta">${esc(store.t("healthNow", { t: (h.now_utc ?? "").slice(0, 19).replace("T", " ") }))}</p></details>`;
            const configuredIntervalMs = (h.configuration?.telemetry_interval_seconds ?? 1) * 1000;
            scheduleHealthPoll(opts.healthIntervalMs ?? configuredIntervalMs);
        }
        catch (error) {
            if (lifetime.isDisposed || request !== healthRequest || healthController?.signal.aborted)
                return;
            strip.hidden = false;
            statusBar.innerHTML = `<span class="status-bar-item">! ${esc(store.t("statusUnavailable"))}</span>`;
            strip.innerHTML = `<span class="warn">${esc(store.t("statusUnavailable"))}</span><button type="button" class="button button-secondary" id="health-retry">${esc(store.t("retry"))}</button>`;
            strip.querySelector("#health-retry")?.addEventListener("click", () => void renderHealth());
            scheduleHealthPoll(opts.healthIntervalMs ?? 1000);
            console.warn("health check failed", error);
        }
    };
    lifetime.add(disposable(store.on("checked", () => void renderHealth())));
    lifetime.add(disposable(() => healthController?.abort()));
    void renderHealth();
    if (opts.healthIntervalMs !== 0)
        lifetime.addInterval(() => { void refreshRuns(); }, 30000);
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
// Real browser: auto-start. Tests import this module with a ?boot=N query
// (to control instantiation) and call boot() themselves — no side effects on import.
if (new URL(import.meta.url).searchParams.get("boot") === null) {
    void boot();
}
