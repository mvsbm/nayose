import { bindPanelSplit } from "./panel-layout.js";
const SEARCH_SPLIT_DEFAULT = 440;
const SEARCH_SPLIT_MIN = 280;
const SEARCH_SPLIT_MAX = 720;
import { esc } from "./schema.js";
import { bindSearchDetail, clearSearchDetail, createSearchDetailState, loadSearchDetail, renderSearchDetail } from "./search-workspace.js";
export async function mountMatchingSearch(el, port, store, viewScope) {
    const t = (key) => esc(store.t(key));
    el.innerHTML = `<div class="panel"><section class="card search-workspace-bar"><div class="search-workspace-bar-input"><span class="search-workspace-icon" aria-hidden="true">⌕</span><label class="search-workspace-visually-hidden" for="q">${t("searchTitle")}</label><input id="q" type="search" autocomplete="off" aria-controls="results" placeholder="${t("searchPlaceholder")}"><span class="search-workspace-shortcut" aria-hidden="true">⌘K</span></div><div class="search-workspace-meta is-empty"><p id="search-correction" class="search-workspace-correction" aria-live="polite"></p><div id="search-filters" class="search-workspace-filters" role="group" aria-label="${t("searchFilters")}"></div><div class="search-workspace-status"><p id="norm-line" class="members" aria-live="polite"></p><p id="stat" role="status" aria-live="polite"></p></div></div></section><div class="search-workspace" data-panel-split="search-workspace"><section class="card search-workspace-results"><div id="results"></div></section><div class="panel-resizer" data-panel-resizer="search-workspace" role="separator" aria-orientation="vertical" aria-label="${t("resizePanel")}" aria-valuemin="280" aria-valuemax="720" aria-valuenow="440" tabindex="0"></div><aside id="search-detail" class="card search-workspace-detail" aria-live="polite"></aside></div></div>`;
    viewScope.add(bindPanelSplit(el, store, {
        id: "search-workspace",
        cssVariable: "--search-results-width",
        defaultWidth: SEARCH_SPLIT_DEFAULT,
        minWidth: SEARCH_SPLIT_MIN,
        maxWidth: SEARCH_SPLIT_MAX,
        pointerSide: "left",
        mobileQuery: "(max-width: 980px)",
    }));
    const query = el.querySelector("#q");
    query.value = store.searchQuery;
    const searchBar = el.querySelector(".search-workspace-bar");
    let lastScrollY = Math.max(0, window.scrollY);
    const syncSearchBar = () => {
        const scrollY = Math.max(0, window.scrollY);
        if (scrollY <= 24)
            searchBar.classList.remove("is-compact");
        else if (scrollY >= lastScrollY)
            searchBar.classList.add("is-compact");
        lastScrollY = scrollY;
    };
    viewScope.addEventListener(window, "scroll", syncSearchBar, { passive: true });
    syncSearchBar();
    const detailHost = el.querySelector("#search-detail");
    const detailState = createSearchDetailState();
    const setSelectedUrl = (id) => {
        const url = new URL(location.href);
        url.hash = id ? `#/search?entity=${encodeURIComponent(id)}` : "#/search";
        window.history.replaceState(null, "", url);
    };
    const closeDetail = () => { setSelectedUrl(""); clearSearchDetail(detailState, paintDetail); };
    const clearDetailForNewQuery = () => {
        if (!detailState.selectedId && !detailState.pending && !detailState.error)
            return;
        setSelectedUrl("");
        clearSearchDetail(detailState, paintDetail);
    };
    const paintDetail = () => {
        detailHost.hidden = !detailState.selectedId && !detailState.pending && !detailState.error;
        detailHost.innerHTML = renderSearchDetail(detailState, store);
        bindSearchDetail(detailHost, closeDetail);
    };
    paintDetail();
    let timer = 0;
    let normTimer = 0;
    let sequence = 0;
    const facetType = [];
    let facetMulti = false;
    let lastRes = { rows: [], semantic: false, suggestion: null };
    const activeFilters = () => ({
        party_type: facetType.length === 1 && (facetType[0] === "person" || facetType[0] === "company") ? facetType[0] : undefined,
        min_record_count: facetMulti ? 2 : undefined,
    });
    const searchNow = (value, request, delay) => {
        window.clearTimeout(timer);
        timer = viewScope.addTimeout(async () => {
            const stat = el.querySelector("#stat");
            stat.textContent = store.t("searching");
            try {
                const res = await port.entities(value, 50, activeFilters());
                if (viewScope.isDisposed || request !== sequence || !el.isConnected || !el.contains(query))
                    return;
                lastRes = res;
                renderResults();
            }
            catch (err) {
                if (!viewScope.isDisposed && request === sequence)
                    stat.textContent = `${store.t("error")}: ${store.t(String(err.message ?? err))}`;
            }
        }, delay);
    };
    const renderResults = () => {
        const results = el.querySelector("#results");
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
            return `<a class="card hit" href="#/search?entity=${encodeURIComponent(hit.nayose_id)}" data-entity-select="${esc(hit.nayose_id)}"><div class="search-workspace-hit-heading"><h3>${esc(hit.name_kanji)} <span class="muted">${esc(hit.name_kana ?? "")}</span></h3><span class="search-workspace-hit-chevron" aria-hidden="true">›</span></div><p class="members">${esc(hit.name_romaji ?? "")}</p><p class="search-workspace-hit-meta">${esc(hit.phone ?? "")} · ${esc(hit.postal_code ?? "")} · ${hit.record_count ?? 0}${hit.prefecture ? ` · ${esc(hit.prefecture)}` : ""}</p>${matchedValues ? `<div class="search-workspace-hit-matches">${matchedValues}</div>` : ""}<div class="search-workspace-hit-signals"><span class="chip-state ${kind === "exact" ? "ok" : kind === "nearest" ? "warn" : "ok"}">${esc(store.t(kind === "exact" ? "exactSignal" : kind === "nearest" ? "nearestSignal" : "semanticSignal"))}${hit.score != null ? ` ${(hit.score * 100).toFixed(0)}%` : ""}</span>${(hit.signals ?? []).map(signal => `<span class="chip-state">${esc(store.t(signal))} ${esc(store.t(kind === "exact" ? "exactChip" : "signalChip"))}</span>`).join("")}${(hit.twin_count ?? 0) > 0 ? `<span class="chip-state warn">${esc(store.t("twinBadge", { n: hit.twin_count ?? 0 }))}</span>` : ""}</div></a>`;
        };
        const group = (title, hits, kind) => {
            return hits.length ? `<section class="search-suggestions-group"><div class="search-suggestions-heading"><h3>${esc(title)}</h3><span class="members">${hits.length}</span></div>${hits.map(hit => card(hit, kind)).join("")}</section>` : "";
        };
        const correction = el.querySelector("#search-correction");
        if (correction)
            correction.innerHTML = lastRes.suggestion ? `<strong>${esc(store.t("suggestLine"))}</strong> <a href="#/search" data-suggest="${esc(lastRes.suggestion.term)}">${esc(lastRes.suggestion.term)}</a> <span class="members">· ${(lastRes.suggestion.score * 100).toFixed(0)}%</span>` : "";
        const filter = (id, label, active) => `<button type="button" class="search-workspace-filter" data-filter="${id}" aria-pressed="${active}">${esc(label)}</button>`;
        const filterHost = el.querySelector("#search-filters");
        el.querySelector(".search-workspace-meta").classList.remove("is-empty");
        filterHost.innerHTML = `<span class="search-workspace-filter-label">${t("searchFilters")}</span>${filter("person", store.t("facetPerson"), facetType.includes("person"))}${filter("company", store.t("facetCompany"), facetType.includes("company"))}${filter("multi", store.t("facetMulti"), facetMulti)}<span id="search-mode" class="members">${esc(store.t(mode))}${esc(backend)}</span>`;
        results.innerHTML = `<section class="search-suggestions" aria-live="polite">${group(store.t("exactMatches"), exact, "exact")}${group(store.t("semanticMatches"), semantic, "semantic")}${group(store.t("nearestMatches"), nearest, "nearest")}${!all.length ? `<p class="members">${esc(store.t("noSearchResults"))}</p>` : ""}</section>`;
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
        void loadSearchDetail(detailHost, detailState, id, port, store, paintDetail);
    });
    query.addEventListener("input", () => {
        store.searchQuery = query.value;
        window.clearTimeout(timer);
        window.clearTimeout(normTimer);
        const request = ++sequence;
        const value = query.value;
        clearDetailForNewQuery();
        el.querySelector(".search-workspace-meta").classList.add("is-empty");
        el.querySelector("#search-correction").innerHTML = "";
        if (value.trim().length < 2) {
            el.querySelector("#stat").textContent = "";
            el.querySelector("#results").innerHTML = `<div class="search-workspace-empty"><p>${t("searchPrompt")}</p></div>`;
            el.querySelector("#norm-line").textContent = "";
            el.querySelector("#search-correction").innerHTML = "";
            el.querySelector("#search-filters").innerHTML = "";
            lastRes = { rows: [], semantic: false, suggestion: null };
            return;
        }
        normTimer = viewScope.addTimeout(async () => {
            try {
                const norm = await port.normalizePreview(value.trim());
                if (viewScope.isDisposed || request !== sequence || !el.isConnected)
                    return;
                const tokens = Object.entries(norm).filter(([k, v]) => v && v !== value.trim() && ["name_kanji", "name_kana", "phone", "postal_code"].includes(k));
                const line = el.querySelector("#norm-line");
                if (line)
                    line.textContent = tokens.length ? `${store.t("normHint")}: ${tokens.map(([k, v]) => `${store.t(k)}→${v}`).join(" · ")}` : "";
            }
            catch { /* preview optional */ }
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
}
