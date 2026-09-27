import { bindPanelSplit } from "./panel-layout.js";
import { asNumber } from "./core.js";
import { presentError } from "./api-error.js";
import { findViewport } from "./viewport.js";
const SEARCH_SPLIT_DEFAULT = 440;
const SEARCH_SPLIT_MIN = 0;
const SEARCH_SPLIT_MAX = 720;
import { esc } from "./schema.js";
import { icon } from "./icons.js";
import { bindSearchDetail, clearSearchDetail, createSearchDetailState, loadSearchDetail, renderSearchDetail } from "./search-workspace.js";
export async function mountMatchingSearch(el, port, store, viewScope) {
    const t = (key) => esc(store.t(key));
    el.innerHTML = `<div class="panel search-workspace-page"><section class="card search-workspace-bar"><div class="search-workspace-bar-input"><span class="search-workspace-icon">${icon("search")}</span><label class="search-workspace-visually-hidden" for="q">${t("searchTitle")}</label><input class="text-input" id="q" type="search" autocomplete="off" aria-controls="results" placeholder="${t("searchPlaceholder")}"><span class="search-workspace-shortcut" aria-hidden="true">⌘K</span><details id="search-filter-menu" class="search-workspace-filter-menu" hidden><summary class="button button-toolbar search-workspace-filter-toggle" aria-controls="search-filters" aria-label="${t("searchFilters")}">${icon("filter")}<span class="search-workspace-filter-toggle-label">${t("searchFilters")}</span></summary><div id="search-filters" class="search-workspace-filters" role="group" aria-label="${t("searchFilters")}"></div></details></div><div class="search-workspace-meta is-empty"><p id="search-correction" class="search-workspace-correction" aria-live="polite"></p><div class="search-workspace-status"><p id="norm-line" class="text-meta" aria-live="polite"></p><p id="stat" role="status" aria-live="polite"></p></div></div></section><nayose-split-pane class="search-workspace" data-panel-split="search-workspace"><nayose-viewport slot="first" class="card viewport search-workspace-results" aria-busy="false"><div id="results"><div class="search-workspace-empty"><span class="search-workspace-empty-icon">${icon("search")}</span><p>${t("searchPrompt")}</p></div></div></nayose-viewport><div slot="sash" class="panel-resizer sash" data-panel-resizer="search-workspace" role="separator" aria-orientation="vertical" aria-label="${t("resizePanel")}" aria-valuemin="${SEARCH_SPLIT_MIN}" aria-valuemax="${SEARCH_SPLIT_MAX}" aria-valuenow="${SEARCH_SPLIT_DEFAULT}" tabindex="0"></div><nayose-viewport slot="second" id="search-detail" class="card viewport search-workspace-detail"></nayose-viewport></nayose-split-pane></div>`;
    viewScope.add(bindPanelSplit(el, store, {
        id: "search-workspace",
        cssVariable: "--search-results-width",
        defaultWidth: SEARCH_SPLIT_DEFAULT,
        minWidth: SEARCH_SPLIT_MIN,
        maxWidth: SEARCH_SPLIT_MAX,
        getMaxWidth: () => {
            const workspace = el.querySelector("[data-panel-split='search-workspace']");
            const sash = workspace?.querySelector("[data-panel-resizer]");
            if (!workspace || !sash || workspace.clientWidth === 0)
                return SEARCH_SPLIT_MAX;
            const gap = Number.parseFloat(getComputedStyle(workspace).columnGap) || 0;
            return Math.max(SEARCH_SPLIT_MIN, workspace.clientWidth - sash.clientWidth - gap * 2);
        },
        pointerSide: "left",
        mobileQuery: "(max-width: 980px)",
    }));
    const query = el.querySelector("#q");
    const initialParams = new URLSearchParams(location.hash.split("?")[1] ?? "");
    query.value = initialParams.get("q") ?? store.searchQuery;
    store.searchQuery = query.value;
    const searchBar = el.querySelector(".search-workspace-bar");
    const split = el.querySelector("[data-panel-split='search-workspace']");
    let expandedSearchBarHeight = searchBar.getBoundingClientRect().height;
    const viewport = findViewport(el, ".search-workspace-results");
    const scrollPosition = () => viewport?.scrollTop ?? Math.max(0, window.scrollY);
    let lastScrollY = scrollPosition();
    const syncSearchBar = () => {
        const scrollY = scrollPosition();
        if (!searchBar.classList.contains("is-compact"))
            expandedSearchBarHeight = searchBar.getBoundingClientRect().height;
        if (scrollY <= 8 || scrollY < lastScrollY - 3)
            searchBar.classList.remove("is-compact");
        else if (scrollY > lastScrollY + 3)
            searchBar.classList.add("is-compact");
        const currentSearchBarHeight = searchBar.getBoundingClientRect().height;
        split.style.setProperty("--search-sash-offset", `${Math.max(0, expandedSearchBarHeight - currentSearchBarHeight) / 2}px`);
        lastScrollY = scrollY;
    };
    if (viewport) {
        viewScope.add(viewport);
        viewScope.add(viewport.onScroll(syncSearchBar, { passive: true }));
    }
    else {
        viewScope.addEventListener(window, "scroll", syncSearchBar, { passive: true });
    }
    syncSearchBar();
    const detailHost = el.querySelector("#search-detail");
    const detailState = createSearchDetailState();
    const requestedFacet = initialParams.get("party_type");
    const facetType = requestedFacet === "person" || requestedFacet === "company" ? [requestedFacet] : [];
    let facetMulti = initialParams.get("multi") === "1";
    const syncSearchUrl = (selected = "") => {
        const params = new URLSearchParams();
        if (query.value.trim())
            params.set("q", query.value.trim());
        if (facetType.length === 1)
            params.set("party_type", facetType[0]);
        if (facetMulti)
            params.set("multi", "1");
        if (selected)
            params.set("entity", selected);
        const url = new URL(location.href);
        url.hash = `#/search${params.toString() ? `?${params.toString()}` : ""}`;
        window.history.replaceState(null, "", url);
    };
    const setSelectedUrl = (id) => syncSearchUrl(id);
    const closeDetail = () => { setSelectedUrl(""); clearSearchDetail(detailState, paintDetail); renderResults(); };
    const clearDetailForNewQuery = () => {
        if (!detailState.selectedId && detailState.request.kind === "idle")
            return;
        setSelectedUrl("");
        clearSearchDetail(detailState, paintDetail);
    };
    const paintDetail = () => {
        detailHost.hidden = !detailState.selectedId && detailState.request.kind === "idle";
        detailHost.innerHTML = renderSearchDetail(detailState, store);
        bindSearchDetail(detailHost, closeDetail);
    };
    paintDetail();
    let timer = 0;
    let normTimer = 0;
    let searchController = null;
    let normController = null;
    let sequence = 0;
    let lastRes = { rows: [], semantic: false, suggestion: null };
    const activeFilters = () => ({
        party_type: facetType.length === 1 && (facetType[0] === "person" || facetType[0] === "company") ? facetType[0] : undefined,
        min_record_count: facetMulti ? 2 : undefined,
    });
    const setSearchLoading = (loading) => {
        const resultsHost = el.querySelector(".search-workspace-results");
        const results = el.querySelector("#results");
        resultsHost.setAttribute("aria-busy", String(loading));
        if (loading) {
            results.innerHTML = `<div class="search-workspace-loading" role="status" aria-label="${t("searching")}"><p class="text-meta">${t("searching")}</p>${[1, 2, 3, 4].map(() => `<div class="search-workspace-loading-row" aria-hidden="true"><span></span><span></span><span></span></div>`).join("")}</div>`;
        }
    };
    const searchNow = (value, request, delay) => {
        viewScope.clearTimeout(timer);
        timer = viewScope.addTimeout(async () => {
            const stat = el.querySelector("#stat");
            setSearchLoading(true);
            stat.textContent = store.t("searching");
            searchController?.abort();
            searchController = new AbortController();
            const controller = searchController;
            const abort = () => controller.abort();
            viewScope.signal.addEventListener("abort", abort, { once: true });
            try {
                const res = await port.entities(value, 50, activeFilters(), controller.signal);
                if (viewScope.isDisposed || request !== sequence || !el.isConnected || !el.contains(query))
                    return;
                lastRes = res;
                renderResults();
            }
            catch (err) {
                if (!viewScope.isDisposed && request === sequence && !controller.signal.aborted) {
                    const resultsHost = el.querySelector(".search-workspace-results");
                    resultsHost.setAttribute("aria-busy", "false");
                    el.querySelector("#results").innerHTML = `<div class="search-workspace-empty" role="alert"><p>${esc(store.t("error"))}: ${esc(presentError(err, store.t.bind(store)))}</p></div>`;
                    stat.textContent = `${store.t("error")}: ${presentError(err, store.t.bind(store))}`;
                }
            }
            finally {
                viewScope.signal.removeEventListener("abort", abort);
                if (searchController === controller)
                    searchController = null;
            }
        }, delay);
    };
    const renderResults = () => {
        const results = el.querySelector("#results");
        el.querySelector(".search-workspace-results").setAttribute("aria-busy", "false");
        const hasKinds = lastRes.rows.some(hit => hit.kind);
        const exact = lastRes.exact ?? (hasKinds ? lastRes.rows.filter(hit => hit.kind === "exact") : lastRes.semantic ? [] : lastRes.rows);
        const semantic = lastRes.semantic_results ?? (hasKinds ? lastRes.rows.filter(hit => hit.kind === "semantic") : lastRes.semantic ? lastRes.rows : []);
        const seen = new Set([...exact, ...semantic].map(hit => hit.nayose_id));
        const nearest = (lastRes.nearest ?? []).filter(hit => !seen.has(hit.nayose_id));
        const all = [...exact, ...semantic, ...nearest];
        const stat = el.querySelector("#stat");
        stat.textContent = store.t("count", { n: all.length });
        const mode = lastRes.mode === "hybrid" ? "searchModeHybrid" : lastRes.mode === "exact_plus_fuzzy" ? "searchModeFuzzy" : "engineExactOnly";
        const backend = lastRes.embedding_backend ? ` · ${store.t(lastRes.embedding_backend)}` : "";
        const card = (hit, kind) => {
            const matchedValues = (hit.signals ?? []).map(signal => {
                const raw = hit[signal];
                return raw && !["name_kanji", "name_kana", "name_romaji", "phone", "postal_code"].includes(signal)
                    ? `<span class="search-workspace-hit-match">${esc(store.t(signal))}: ${esc(String(raw))}</span>`
                    : "";
            }).join("");
            const selected = detailState.selectedId === hit.nayose_id;
            return `<a class="hit" href="#/search?entity=${encodeURIComponent(hit.nayose_id)}" data-entity-select="${esc(hit.nayose_id)}"${selected ? ` aria-current="true"` : ""}><div class="search-workspace-hit-heading"><h3>${esc(hit.name_kanji)} <span class="muted">${esc(hit.name_kana ?? "")}</span></h3><span class="search-workspace-hit-chevron">${icon("chevron-right")}</span></div><p class="text-meta">${esc(hit.name_romaji ?? "")}</p><p class="search-workspace-hit-meta">${esc(hit.phone ?? "")} · ${esc(hit.postal_code ?? "")} · ${esc(asNumber(hit.record_count))}${hit.prefecture ? ` · ${esc(hit.prefecture)}` : ""}</p>${matchedValues ? `<div class="search-workspace-hit-matches">${matchedValues}</div>` : ""}<div class="search-workspace-hit-signals"><span class="chip-state ${kind === "exact" ? "ok" : kind === "nearest" ? "warn" : "ok"}">${esc(store.t(kind === "exact" ? "exactSignal" : kind === "nearest" ? "nearestSignal" : "semanticSignal"))}${hit.score != null ? ` ${esc((asNumber(hit.score) * 100).toFixed(0))}%` : ""}</span>${(hit.signals ?? []).map(signal => `<span class="chip-state">${esc(store.t(signal))} ${esc(store.t(kind === "exact" ? "exactChip" : "signalChip"))}</span>`).join("")}${(hit.twin_count ?? 0) > 0 ? `<span class="chip-state warn">${esc(store.t("twinBadge", { n: asNumber(hit.twin_count) }))}</span>` : ""}</div></a>`;
        };
        const group = (title, hits, kind) => {
            return hits.length ? `<section class="search-suggestions-group"><div class="search-suggestions-heading"><h3>${esc(title)}</h3><span class="text-meta">${hits.length}</span></div>${hits.map(hit => card(hit, kind)).join("")}</section>` : "";
        };
        const correction = el.querySelector("#search-correction");
        if (correction)
            correction.innerHTML = lastRes.suggestion ? `<strong>${esc(store.t("suggestLine"))}</strong> <a href="#/search" data-suggest="${esc(lastRes.suggestion.term)}">${esc(lastRes.suggestion.term)}</a> <span class="text-meta">· ${(lastRes.suggestion.score * 100).toFixed(0)}%</span>` : "";
        const filter = (id, label, active) => `<button type="button" class="button button-filter search-workspace-filter" data-filter="${id}" aria-pressed="${active}">${esc(label)}</button>`;
        const filterHost = el.querySelector("#search-filters");
        el.querySelector(".search-workspace-meta").classList.remove("is-empty");
        el.querySelector("#search-filter-menu").hidden = false;
        filterHost.innerHTML = `<span class="search-workspace-filter-label">${t("searchFilters")}</span>${filter("person", store.t("facetPerson"), facetType.includes("person"))}${filter("company", store.t("facetCompany"), facetType.includes("company"))}${filter("multi", store.t("facetMulti"), facetMulti)}<span id="search-mode" class="text-meta">${esc(store.t(mode))}${esc(backend)}</span>`;
        results.innerHTML = `<section class="search-suggestions" aria-label="${esc(store.t("searchResults"))}">${group(store.t("exactMatches"), exact, "exact")}${group(store.t("semanticMatches"), semantic, "semantic")}${group(store.t("nearestMatches"), nearest, "nearest")}${!all.length ? `<p class="text-meta">${esc(store.t("noSearchResults"))}</p>` : ""}</section>`;
        filterHost.querySelectorAll("[data-filter]").forEach(button => button.addEventListener("click", () => {
            const id = button.dataset.filter ?? "";
            if (id === "multi")
                facetMulti = !facetMulti;
            else if (facetType.includes(id))
                facetType.splice(facetType.indexOf(id), 1);
            else
                facetType.push(id);
            renderResults();
            if (query.value.trim().length >= 2) {
                syncSearchUrl(detailState.selectedId);
                clearDetailForNewQuery();
                sequence += 1;
                searchNow(query.value.trim(), sequence, 0);
            }
        }));
    };
    el.querySelector("#results").addEventListener("click", event => {
        const target = event.target.closest("[data-entity-select]");
        if (!target)
            return;
        event.preventDefault();
        const id = target.dataset.entitySelect ?? "";
        setSelectedUrl(id);
        renderResults();
        void loadSearchDetail(detailHost, detailState, id, port, store, paintDetail, viewScope.signal);
    });
    query.addEventListener("input", () => {
        store.searchQuery = query.value;
        syncSearchUrl();
        viewScope.clearTimeout(timer);
        viewScope.clearTimeout(normTimer);
        searchController?.abort();
        normController?.abort();
        const request = ++sequence;
        const value = query.value;
        clearDetailForNewQuery();
        el.querySelector(".search-workspace-meta").classList.add("is-empty");
        el.querySelector("#search-correction").innerHTML = "";
        if (value.trim().length < 2) {
            el.querySelector("#stat").textContent = "";
            el.querySelector("#results").innerHTML = `<div class="search-workspace-empty"><span class="search-workspace-empty-icon">${icon("search")}</span><p>${t("searchPrompt")}</p></div>`;
            el.querySelector("#norm-line").textContent = "";
            el.querySelector("#search-correction").innerHTML = "";
            el.querySelector("#search-filters").innerHTML = "";
            el.querySelector("#search-filter-menu").hidden = true;
            el.querySelector(".search-workspace-results").setAttribute("aria-busy", "false");
            lastRes = { rows: [], semantic: false, suggestion: null };
            return;
        }
        normTimer = viewScope.addTimeout(async () => {
            normController = new AbortController();
            const controller = normController;
            const abort = () => controller.abort();
            viewScope.signal.addEventListener("abort", abort, { once: true });
            try {
                const norm = await port.normalizePreview(value.trim(), controller.signal);
                if (viewScope.isDisposed || request !== sequence || !el.isConnected)
                    return;
                const tokens = Object.entries(norm).filter(([k, v]) => v && v !== value.trim() && ["name_kanji", "name_kana", "phone", "postal_code"].includes(k));
                const line = el.querySelector("#norm-line");
                if (line)
                    line.textContent = tokens.length ? `${store.t("normHint")}: ${tokens.map(([k, v]) => `${store.t(k)}→${v}`).join(" · ")}` : "";
            }
            catch { /* preview optional */ }
            finally {
                viewScope.signal.removeEventListener("abort", abort);
                if (normController === controller)
                    normController = null;
            }
        }, 250);
        searchNow(value.trim(), request, 150);
    });
    el.addEventListener("click", (event) => {
        const link = event.target.closest("[data-suggest]");
        if (!link)
            return;
        event.preventDefault();
        query.value = link.dataset.suggest ?? "";
        store.searchQuery = query.value;
        query.dispatchEvent(new window.Event("input"));
        query.focus();
    });
    if (query.value)
        query.dispatchEvent(new window.Event("input"));
    const selectedEntity = initialParams.get("entity");
    if (selectedEntity) {
        setSelectedUrl(selectedEntity);
        void loadSearchDetail(detailHost, detailState, selectedEntity, port, store, paintDetail, viewScope.signal);
    }
}
