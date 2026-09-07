// Composition root (hexagonal): build port+store, mount shell, route.
// No side effects at import time; tests call boot({ port, store }) with mocks.
import { createPort } from "./infra.js";
import { Store } from "./store.js";
import { applyAppearance } from "./appearance.js";
import { checkedKey, parseRoute } from "./core.js";
import { mountCompare, mountCompareRuns, mountConfusion, mountEntities, mountEntity, mountMetrics, mountReview, mountSearch, } from "./features.js";
import { mountAdmin } from "./admin-view.js";
import { mountDataEntry } from "./data-entry-view.js";
const VIEWS = {
    search: (v, p, s) => mountSearch(v, p, s),
    review: (v, p, s) => mountReview(v, p, s),
    confusion: (v, p, s) => mountConfusion(v, p, s),
    metrics: (v, p, s) => mountMetrics(v, p, s),
    entities: (v, p, s) => mountEntities(v, p, s),
    entity: (v, p, s, a) => mountEntity(v, p, s, a[0] ?? ""),
    compare: (v, p, s, a) => mountCompare(v, p, s, a[0] ?? "", a[1] ?? ""),
    "compare-runs": (v, p, s) => mountCompareRuns(v, p, s),
    admin: (v, p, s) => mountAdmin(v, p, s),
    "data-entry": (v, p, s) => mountDataEntry(v, p, s),
};
export async function boot(opts = {}) {
    const store = opts.store ?? new Store();
    const port = opts.port ?? createPort();
    const view = document.querySelector("main#view");
    let activeView;
    let navigation = 0;
    const themeKey = (theme) => theme === "auto" ? "themeAuto" : theme === "dark" ? "themeDark" : "themeLight";
    const densityKey = (density) => density === "compact" ? "densityCompact" : "densityComfortable";
    const localizeShell = () => {
        document.documentElement.lang = store.locale;
        document.title = `Nayose — ${store.t("title")}`;
        document.querySelectorAll("[data-i18n]").forEach(el => { el.textContent = store.t(el.dataset.i18n); });
        document.querySelectorAll("[data-i18n-aria]").forEach(el => el.setAttribute("aria-label", store.t(el.dataset.i18nAria)));
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
        const language = document.querySelector("#language");
        language.textContent = store.locale === "ja" ? "English" : "日本語";
        language.lang = store.locale === "ja" ? "en" : "ja";
        language.setAttribute("aria-label", store.locale === "ja" ? "Switch to English" : "日本語に切り替え");
    };
    const applyChromeAppearance = () => {
        applyAppearance(store.theme, store.density);
        localizeShell();
    };
    applyChromeAppearance();
    const unTheme = store.on("theme", applyChromeAppearance);
    const unDensity = store.on("density", applyChromeAppearance);
    const systemTheme = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
    const onSystemTheme = () => { if (store.theme === "auto")
        applyChromeAppearance(); };
    systemTheme?.addEventListener("change", onSystemTheme);
    const paint = async (html) => {
        view.innerHTML = `<div class="panel">${html}</div>`;
        view.focus({ preventScroll: true });
    };
    const navigate = async () => {
        const ticket = ++navigation;
        activeView?.dispatchEvent(new window.Event("unmount"));
        const target = document.createElement("div");
        activeView = target;
        view.replaceChildren(target);
        if (!store.runs.length) {
            await paint(`<div class="empty"><h2>${esc(store.t("noRun"))}</h2><p>${esc(store.t("noRunHelp"))}</p></div>`);
            return;
        }
        const route = parseRoute(location.hash);
        const navRoute = route.name === "entity" ? "entities" : route.name === "compare" ? "review" : route.name;
        document.querySelectorAll(".side a").forEach((a) => a.setAttribute("aria-current", a.getAttribute("data-nav") === navRoute ? "page" : ""));
        document.querySelectorAll(".side details[data-nav-group]").forEach((group) => {
            if (group.querySelector(`[data-nav="${navRoute}"]`))
                group.open = true;
        });
        window.scrollTo(0, 0);
        const render = VIEWS[route.name];
        try {
            await (render ? render(target, port, store, route.args) : mountSearch(target, port, store));
        }
        catch (err) {
            if (ticket !== navigation)
                return;
            target.innerHTML = `<div class="empty"><p>${esc(store.t("error"))}: ${esc(store.t(String(err.message ?? err)))}</p><button class="more" id="retry">${esc(store.t("retry"))}</button></div>`;
            target.querySelector("#retry")?.addEventListener("click", () => void navigate());
        }
    };
    const onHashChange = () => { void navigate(); };
    window.addEventListener("hashchange", onHashChange);
    const switchLanguage = () => store.setLocale(store.locale === "ja" ? "en" : "ja");
    document.querySelector("#language").addEventListener("click", switchLanguage);
    const unLocale = store.on("locale", () => { localizeShell(); void navigate(); });
    let unCheckedStatus = () => { };
    const cleanup = () => {
        activeView?.dispatchEvent(new window.Event("unmount"));
        window.removeEventListener("hashchange", onHashChange);
        document.querySelector("#language")?.removeEventListener("click", switchLanguage);
        unLocale();
        unTheme();
        unDensity();
        systemTheme?.removeEventListener("change", onSystemTheme);
        if (healthTimer)
            window.clearInterval(healthTimer);
        unCheckedStatus();
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
    sel.addEventListener("change", () => {
        store.setRun(sel.value);
        store.write("nayose.run", store.run);
        port.run(store.run);
        store.clearRunState();
        const p = store.runs.find((r) => r.id === store.run);
        fp.textContent = p?.fingerprint ? `fingerprint ${esc(p.fingerprint)}` : "";
        void navigate();
    });
    // Admin and data entry are local-first surfaces with no separate credential.
    try {
        const cap = await port.adminCapability().catch(() => ({ capable: false }));
        if (cap.capable) {
            document.querySelectorAll("#nav-admin-group, #nav-data-entry").forEach(n => n.removeAttribute("hidden"));
        }
        try {
            localStorage.removeItem("nayose.adminToken");
        }
        catch { /* storage unavailable */ }
    }
    catch { /* server unavailable: keep the nav hidden */ }
    // decision persistence: localStorage (offline) + server-side (survives machines)
    store.checked = new Set(JSON.parse(store.read("nayose.checked") ?? "[]"));
    store.reviewer = store.read("nayose.reviewer") ?? "";
    store.on("checked", () => store.write("nayose.checked", JSON.stringify([...store.checked])));
    try {
        const remote = await port.loadDecisions();
        if (remote.length) {
            store.checked = new Set(remote.map((d) => checkedKey(store.run, d.left, d.right)));
        }
    }
    catch {
        /* server unreachable -> keep localStorage state */
    }
    let saveTimer;
    store.on("checked", () => {
        store.write("nayose.checked", JSON.stringify([...store.checked]));
        window.clearTimeout(saveTimer);
        saveTimer = window.setTimeout(() => {
            const now = new Date().toISOString();
            const items = [...store.checked].map((key) => {
                const parts = key.split("|");
                return { left: parts[1] ?? "", right: parts[2] ?? "", ts: now, reviewer: store.reviewer };
            });
            void port.saveDecisions(items).catch(() => { });
        }, 500);
    });
    // appearance preferences are state transitions projected by applyChromeAppearance
    document.querySelector("#theme")?.addEventListener("click", () => store.toggleTheme());
    document.querySelector("#density")?.addEventListener("click", () => store.toggleDensity());
    // E — system status strip (visibility of system status): health poll drives it
    const strip = document.querySelector("#status-strip");
    const renderHealth = async () => {
        try {
            const h = await port.health();
            if (!strip.isConnected)
                return;
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
        }
        catch { /* server unreachable: keep the strip silent */ }
    };
    unCheckedStatus = store.on("checked", () => void renderHealth());
    void renderHealth();
    const healthIntervalMs = opts.healthIntervalMs ?? 30000;
    const healthTimer = healthIntervalMs > 0 ? window.setInterval(() => void renderHealth(), healthIntervalMs) : 0;
    // ⌘K palette (keyboard-first) — powered by the same QueryPort
    const palette = document.querySelector("#palette");
    const pInput = document.querySelector("#palette-input");
    const pList = document.querySelector("#palette-results");
    const pStat = document.querySelector("#palette-stat");
    let pItems = [];
    let pActive = -1;
    let paletteRequest = 0;
    let focusBeforePalette = null;
    const openPalette = () => {
        const active = document.activeElement;
        const opener = document.querySelector("#cmd");
        focusBeforePalette = active && active !== document.body && active !== document.documentElement && typeof active.focus === "function" ? active : opener;
        palette.classList.add("open");
        palette.hidden = false;
        pInput.value = "";
        pStat.textContent = "";
        void paletteSearch("");
        pInput.focus();
    };
    const closePalette = () => {
        paletteRequest += 1;
        palette.classList.remove("open");
        palette.hidden = true;
        pActive = -1;
        const restore = focusBeforePalette;
        focusBeforePalette = null;
        const target = restore?.isConnected ? restore : document.querySelector("#cmd");
        target?.focus();
    };
    const paletteActions = () => [
        { label: store.t("actionThemeLight"), meta: "action:theme-light", go: () => { store.setTheme("light"); closePalette(); } },
        { label: store.t("actionThemeDark"), meta: "action:theme-dark", go: () => { store.setTheme("dark"); closePalette(); } },
        { label: store.t("actionThemeAuto"), meta: "action:theme-auto", go: () => { store.setTheme("auto"); closePalette(); } },
        { label: store.t("actionDensityComfortable"), meta: "action:density-comfortable", go: () => { store.setDensity("comfortable"); closePalette(); } },
        { label: store.t("actionDensityCompact"), meta: "action:density-compact", go: () => { store.setDensity("compact"); closePalette(); } },
        { label: store.t("actionHelp"), meta: "action:help", go: () => { pStat.textContent = store.t("commandHelp"); } },
    ];
    async function paletteSearch(q) {
        const request = ++paletteRequest;
        const query = q.trim().toLocaleLowerCase();
        const nav = [["search", store.t("search")], ["review", store.t("review")], ["entities", store.t("entityExplorer")], ["confusion", store.t("confusion")], ["metrics", store.t("metrics")], ["compare-runs", store.t("compareRuns")]];
        if (!document.querySelector("#nav-data-entry[hidden]"))
            nav.push(["data-entry", store.t("dataEntry")]);
        if (!document.querySelector("#nav-admin[hidden]"))
            nav.push(["admin", store.t("dataTrain")]);
        const items = nav.map(([id, label]) => ({ label, meta: `#/${id}`, go: () => { closePalette(); location.hash = `#/${id}`; } }));
        items.push(...paletteActions().filter(item => !query || `${item.label} ${item.meta}`.toLocaleLowerCase().includes(query)));
        if (q.length >= 2) {
            pStat.textContent = store.t("searching");
            try {
                const res = await port.entities(q);
                if (request !== paletteRequest)
                    return;
                res.rows.forEach((h) => items.push({ label: `${h.name_kanji} ${h.name_kana ?? ""}`, meta: h.nayose_id, go: () => { closePalette(); location.hash = `#/entity/${h.nayose_id}`; } }));
                pStat.textContent = store.t("count", { n: res.rows.length });
            }
            catch (err) {
                if (request !== paletteRequest)
                    return;
                pStat.textContent = store.t("error") + ": " + String(err?.message ?? err);
            }
        }
        if (request !== paletteRequest)
            return;
        pItems = items.slice(0, 14);
        pActive = pItems.length ? 0 : -1;
        renderPaletteList();
    }
    const renderPaletteList = () => {
        pList.innerHTML = pItems
            .map((it, i) => `<li role="option" id="p-${i}" aria-selected="${i === pActive}" data-i="${i}"><span>${esc(it.label)}</span><span class="muted">${esc(it.meta)}</span></li>`)
            .join("");
        pList.setAttribute("aria-activedescendant", pActive >= 0 ? `p-${pActive}` : "");
        pList.querySelector(`[data-i="${pActive}"]`)?.scrollIntoView({ block: "nearest" });
    };
    pInput.addEventListener("input", () => void paletteSearch(pInput.value));
    pInput.addEventListener("keydown", (e) => {
        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
            e.preventDefault();
            if (pItems.length) {
                pActive = (pActive + (e.key === "ArrowDown" ? 1 : pItems.length - 1)) % pItems.length;
                renderPaletteList();
            }
        }
        else if (e.key === "Enter") {
            e.preventDefault();
            if (pActive >= 0 && pItems[pActive])
                pItems[pActive].go();
        }
        else if (e.key === "Escape") {
            closePalette();
        }
    });
    pList.addEventListener("click", (e) => {
        const li = e.target.closest("li");
        const i = Number(li?.dataset.i);
        if (pItems[i])
            pItems[i].go();
    });
    const paletteKey = (e) => {
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
            e.preventDefault();
            openPalette();
        }
        else if (!palette.hidden && e.key === "Tab") {
            e.preventDefault();
            pInput.focus();
        }
        else if (!palette.hidden && e.key === "Escape") {
            closePalette();
        }
    };
    document.addEventListener("keydown", paletteKey);
    document.querySelector("#cmd")?.addEventListener("click", openPalette);
    palette.addEventListener("click", (e) => { if (e.target === palette)
        closePalette(); });
    await navigate();
    return { destroy: () => { cleanup(); document.removeEventListener("keydown", paletteKey); document.querySelector("#cmd")?.removeEventListener("click", openPalette); } };
}
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// Real browser: auto-start. Tests import this module with a ?boot=N query
// (to control instantiation) and call boot() themselves — no side effects on import.
if (new URL(import.meta.url).searchParams.get("boot") === null) {
    void boot();
}
