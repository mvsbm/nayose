import { asNumber } from "./core.js";
import { esc } from "./schema.js";
import { bindPanelSplit } from "./panel-layout.js";
const ENTITY_SPLIT_DEFAULT = 520;
const ENTITY_SPLIT_MIN = 320;
const ENTITY_SPLIT_MAX = 800;
const PAGE_SIZES = [25, 50, 100, 200];
const SORTS = ["relevance", "records", "name", "last_seen"];
function stateFromUrl() {
    const params = new URLSearchParams(location.hash.split("?")[1] ?? "");
    const requestedSize = Number(params.get("size"));
    const requestedSort = params.get("sort");
    const requestedParty = params.get("party_type");
    const partyType = requestedParty === "person" || requestedParty === "company" ? requestedParty : "";
    return {
        query: params.get("q") ?? "",
        offset: Math.max(0, Math.floor(Number(params.get("offset")) || 0)),
        cursor: params.get("cursor") ?? "",
        size: PAGE_SIZES.includes(requestedSize) ? requestedSize : 50,
        sort: SORTS.includes(requestedSort) ? requestedSort : "records",
        partyType,
        minRecords: Math.max(0, Number(params.get("min_records")) || 0),
        selected: params.get("selected") ?? "",
        potential: params.get("potential") === "1",
    };
}
function filtersFrom(state) {
    return {
        ...(state.partyType ? { party_type: state.partyType } : {}),
        ...(state.minRecords ? { min_record_count: state.minRecords } : {}),
    };
}
function hashFor(state, selected = state.selected) {
    const params = new URLSearchParams();
    if (state.query)
        params.set("q", state.query);
    if (state.offset)
        params.set("offset", String(state.offset));
    if (state.cursor)
        params.set("cursor", state.cursor);
    if (state.size !== 50)
        params.set("size", String(state.size));
    if (state.sort !== "records")
        params.set("sort", state.sort);
    if (state.partyType)
        params.set("party_type", state.partyType);
    if (state.minRecords)
        params.set("min_records", String(state.minRecords));
    if (selected)
        params.set("selected", selected);
    if (state.potential)
        params.set("potential", "1");
    const query = params.toString();
    return `#/entities${query ? `?${query}` : ""}`;
}
function display(value, missing) {
    const text = value == null ? "" : String(value);
    return text || missing;
}
function resultRows(page, state, store) {
    return page.rows.map((row) => `<tr class="${row.nayose_id === state.selected ? "selected" : ""}" data-entity-select="${esc(row.nayose_id)}">
    <th scope="row"><button type="button" class="entity-explorer-select">${esc(row.name_kanji || row.nayose_id)}</button><br><span class="members">${esc(row.nayose_id)}</span></th>
    <td>${esc(row.name_kana || "")}</td><td>${asNumber(row.record_count)}</td>
    <td>${esc(row.party_type || "")}</td><td>${esc(row.last_seen || "")}</td>
    <td><a href="#/entity/${encodeURIComponent(row.nayose_id)}">${esc(store.t("inspectEntity"))}</a></td></tr>`).join("");
}
function renderSearch(page, state, store) {
    const first = page.rows.length ? state.offset + 1 : 0;
    const last = state.offset + page.rows.length;
    const total = page.total_is_exact ? String(page.total) : `${page.total}+`;
    const hasMore = page.has_more ?? (page.total_is_exact ? last < page.total : Boolean(page.next_cursor));
    return `<section class="card entity-explorer-search">
    <div class="toolbar"><div><p class="eyebrow">${esc(store.t("entityExplorerEyebrow"))}</p><h2>${esc(store.t("entityExplorerTitle"))}</h2></div>
      <span class="members" role="status" aria-live="polite">${esc(store.t("entityExplorerRange", { first, last, total }))}</span></div>
    <p>${esc(store.t("entityExplorerHelp"))}</p>
    <form id="entity-explorer-form" class="entity-explorer-form">
      <label class="entity-explorer-query">${esc(store.t("explorerSearch"))}<input id="entity-explorer-query" type="search" value="${esc(state.query)}" placeholder="${esc(store.t("searchPlaceholder"))}"></label>
      <label>${esc(store.t("party_type"))}<select id="entity-explorer-party"><option value="">${esc(store.t("facetAll"))}</option><option value="person" ${state.partyType === "person" ? "selected" : ""}>${esc(store.t("facetPerson"))}</option><option value="company" ${state.partyType === "company" ? "selected" : ""}>${esc(store.t("facetCompany"))}</option></select></label>
      <label>${esc(store.t("minRecords"))}<input id="entity-explorer-min" type="number" min="0" step="1" value="${state.minRecords || ""}"></label>
      <button class="more" type="submit">${esc(store.t("search"))}</button>
    </form>
    <div class="toolbar entity-explorer-controls"><label>${esc(store.t("pageSize"))}<select id="entity-explorer-size">${PAGE_SIZES.map((size) => `<option value="${size}" ${size === state.size ? "selected" : ""}>${size}</option>`).join("")}</select></label>
      <label>${esc(store.t("sort"))}<select id="entity-explorer-sort"><option value="records" ${state.sort === "records" ? "selected" : ""}>${esc(store.t("sortRecords"))}</option><option value="name" ${state.sort === "name" ? "selected" : ""}>${esc(store.t("sortName"))}</option><option value="last_seen" ${state.sort === "last_seen" ? "selected" : ""}>${esc(store.t("sortLastSeen"))}</option><option value="relevance" ${state.sort === "relevance" ? "selected" : ""}>${esc(store.t("sortRelevance"))}</option></select></label>
      <button type="button" class="more" id="entity-explorer-prev" ${state.offset <= 0 ? "disabled" : ""}>${esc(store.t("previous"))}</button><button type="button" class="more" id="entity-explorer-next" ${!hasMore || !page.next_cursor ? "disabled" : ""}>${esc(store.t("next"))}</button>
    </div>
  </section>`;
}
function renderResults(page, state, store) {
    return `<section class="card entity-explorer-results" aria-labelledby="entity-explorer-results-title">
    <div class="toolbar"><h3 id="entity-explorer-results-title">${esc(store.t("explorerResults"))}</h3><span class="members">${esc(store.t(page.search_mode === "hybrid_fallback" ? "hybridFallback" : "exactIndex"))}</span></div>
    <div class="table-scroll" tabindex="0" role="region" aria-label="${esc(store.t("explorerResults"))}"><table><caption class="members">${esc(store.t("entityExplorerTableHelp"))}</caption><thead><tr><th scope="col">${esc(store.t("name_kanji"))}</th><th scope="col">${esc(store.t("name_kana"))}</th><th scope="col">${esc(store.t("record_count"))}</th><th scope="col">${esc(store.t("party_type"))}</th><th scope="col">${esc(store.t("last_seen"))}</th><th scope="col">${esc(store.t("action"))}</th></tr></thead><tbody>${resultRows(page, state, store) || `<tr><td colspan="6" class="members">${esc(store.t("none"))}</td></tr>`}</tbody></table></div>
  </section>`;
}
function loadingPage(state) {
    return {
        rows: [],
        total: 0,
        offset: state.offset,
        limit: state.size,
        query: state.query,
        sort: state.sort,
        filters: { ...filtersFrom(state) },
        query_plan: {},
        search_mode: "exact",
        total_is_exact: true,
        search_trace: {},
    };
}
function renderLoading(state, store) {
    return `<div class="panel entity-explorer">${renderSearch(loadingPage(state), state, store)}<div class="entity-explorer-layout"><section class="card"><p role="status" aria-live="polite">${esc(store.t("entityExplorerLoading"))}</p><div class="skel"><div class="card" style="height:12rem"></div></div></section><aside class="card entity-explorer-detail"><p role="status" aria-live="polite">${esc(store.t("detailLoading"))}</p></aside></div></div>`;
}
const FOOTPRINT_GROUPS = [
    { kind: "record", titleKey: "sourceRecords" },
    { kind: "policy", titleKey: "policies" },
    { kind: "agency", titleKey: "agencies" },
    { kind: "channel", titleKey: "channels" },
];
function nodeLabels(graph) {
    return new Map(graph.nodes.map((node) => [node.id, node.label]));
}
function observedNodes(graph, kind) {
    const connected = new Set();
    graph.edges.filter((edge) => edge.kind === "observed").forEach((edge) => {
        connected.add(edge.source);
        connected.add(edge.target);
    });
    return graph.nodes.filter((node) => node.kind === kind && connected.has(node.id));
}
function footprintGroups(detail, graph) {
    return FOOTPRINT_GROUPS.map(({ kind, titleKey }) => {
        const nodes = observedNodes(graph, kind);
        const field = kind === "record" ? "record_count" : kind === "policy" ? "policy_count" : kind === "agency" ? "agency_count" : "";
        const reported = field && typeof detail.entity[field] === "number" ? Number(detail.entity[field]) : nodes.length;
        return { kind, titleKey, nodes, count: Math.max(0, reported) };
    });
}
function previewLabels(group, store) {
    const labels = group.nodes.slice(0, 3).map((node) => node.label || node.id);
    const remainder = Math.max(0, group.count - labels.length);
    if (remainder)
        labels.push(store.t("moreItems", { n: remainder }));
    return labels.join(" · ") || store.t("none");
}
function renderOverview(detail, groups, state, store) {
    const entity = detail.entity;
    const firstSeen = display(entity.first_seen, store.t("missingValue"));
    const lastSeen = display(entity.last_seen, store.t("missingValue"));
    return `<section class="entity-explorer-overview" aria-labelledby="entity-explorer-detail-title"><div class="entity-explorer-header"><div><p class="eyebrow">${esc(store.t("entityOverview"))}</p><h2 id="entity-explorer-detail-title">${esc(display(entity.name_kanji, state.selected))}</h2><p class="members">${esc(state.selected)} · ${esc(display(entity.party_type, store.t("missingValue")))}</p></div><span class="chip-state">${esc(store.t("selectedEntity"))}</span></div><p class="members">${esc(store.t("entitySummaryDisclaimer"))}</p><div class="entity-explorer-kpis" aria-label="${esc(store.t("entityCoverage"))}">${groups.map((group) => `<div class="entity-explorer-kpi"><span>${esc(store.t(group.titleKey))}</span><strong>${group.count}</strong></div>`).join("")}</div><dl class="entity-explorer-dates"><div><dt>${esc(store.t("first_seen"))}</dt><dd>${esc(firstSeen)}</dd></div><div><dt>${esc(store.t("last_seen"))}</dt><dd>${esc(lastSeen)}</dd></div></dl><a class="more" href="#/entity/${encodeURIComponent(state.selected)}">${esc(store.t("openEntityDetail"))}</a></section>`;
}
function renderFootprint(groups, store) {
    return `<section id="entity-explorer-footprint" class="entity-explorer-footprint" aria-labelledby="entity-explorer-footprint-title"><div class="toolbar"><div><h3 id="entity-explorer-footprint-title">${esc(store.t("entityCoverage"))}</h3><p class="members">${esc(store.t("entityCoverageHelp"))}</p></div><span class="members">${esc(store.t("observedRelationship"))}</span></div><div class="entity-explorer-footprint-grid" role="list">${groups.map((group) => `<div class="entity-explorer-footprint-group" data-footprint-kind="${group.kind}" role="listitem"><div class="entity-explorer-footprint-heading"><span>${esc(store.t(group.titleKey))}</span><strong>${group.count}</strong></div><p class="members">${esc(previewLabels(group, store))}</p></div>`).join("")}</div></section>`;
}
const RELATION_LABELS = {
    member_record: "sourceRecords",
    observed_policy: "policies",
    observed_agency: "agencies",
    source_channel: "channels",
    potential_match: "potentialMatches",
};
function relationLabel(edge, store) {
    return store.t(RELATION_LABELS[edge.relation] ?? "observedLinks");
}
function evidenceText(edge, store) {
    if (edge.kind === "potential") {
        const left = String(edge.evidence.left_record_id || store.t("missingValue"));
        const right = String(edge.evidence.right_record_id || store.t("missingValue"));
        const probability = edge.evidence.pair_probability;
        const score = typeof probability === "number" ? ` · ${(probability * 100).toFixed(1)}%` : "";
        return `${store.t("evidenceReviewQueue", { left, right })}${score}`;
    }
    return store.t("evidenceSourceRecord", { id: String(edge.evidence.record_id || store.t("missingValue")) });
}
function renderEvidenceTable(graph, store, kind) {
    const labels = nodeLabels(graph);
    const edges = graph.edges.filter((edge) => edge.kind === kind);
    const rows = edges.map((edge) => `<tr><td><span class="relationship-badge ${edge.kind}">${esc(store.t(edge.kind === "potential" ? "potentialRelationship" : "observedRelationship"))}</span><br><span class="members">${esc(relationLabel(edge, store))}</span></td><td>${esc(labels.get(edge.target) || edge.target)}</td><td>${esc(evidenceText(edge, store))}</td></tr>`).join("");
    return `<div class="table-scroll entity-explorer-evidence-table" tabindex="0" role="region" aria-label="${esc(store.t("evidenceTable"))}"><table><caption class="members">${esc(store.t("evidenceTableHelp"))}</caption><thead><tr><th scope="col">${esc(store.t("relationType"))}</th><th scope="col">${esc(store.t("connectedItem"))}</th><th scope="col">${esc(store.t("evidence"))}</th></tr></thead><tbody>${rows || `<tr><td colspan="3" class="members">${esc(store.t("none"))}</td></tr>`}</tbody></table></div>`;
}
function renderObservedEvidence(graph, store) {
    const count = graph.edges.filter((edge) => edge.kind === "observed").length;
    return `<details id="entity-explorer-observed" class="entity-explorer-disclosure"><summary><span>${esc(store.t("observedLinks"))}</span><span class="entity-explorer-disclosure-meta">${esc(store.t("count", { n: count }))}</span></summary><p class="members">${esc(store.t("observedEvidence"))}</p>${renderEvidenceTable(graph, store, "observed")}</details>`;
}
function renderPotentialEvidence(graph, state, store) {
    const count = graph.edges.filter((edge) => edge.kind === "potential").length;
    const body = graph.potential_matches_available
        ? `<label class="entity-explorer-potential"><input id="entity-explorer-potential" type="checkbox" ${state.potential ? "checked" : ""}>${esc(store.t("includePotential"))}</label>${state.potential ? renderEvidenceTable(graph, store, "potential") : ""}`
        : `<p class="members">${esc(store.t("potentialUnavailable"))}</p>`;
    return `<details id="entity-explorer-potential-section" class="entity-explorer-disclosure" ${state.potential ? "open" : ""}><summary><span>${esc(store.t("potentialMatches"))}</span><span class="entity-explorer-disclosure-meta">${esc(state.potential ? store.t("count", { n: count }) : store.t("notShown"))}</span></summary><p class="members">${esc(store.t("potentialMatchesHelp"))}</p>${body}</details>`;
}
function renderDetail(detail, graph, state, store) {
    if (!detail || !graph)
        return `<aside class="card entity-explorer-detail"><p class="members" role="status" aria-live="polite">${esc(state.selected ? store.t("detailLoading") : store.t("selectEntity"))}</p></aside>`;
    const groups = footprintGroups(detail, graph);
    return `<aside class="card entity-explorer-detail" data-selected-entity="${esc(state.selected)}">${renderOverview(detail, groups, state, store)}${renderFootprint(groups, store)}${graph.truncated ? `<p class="notice">${esc(store.t("graphTruncated"))}</p>` : ""}${renderObservedEvidence(graph, store)}${renderPotentialEvidence(graph, state, store)}</aside>`;
}
function bindPotential(view, state) {
    view.querySelector("#entity-explorer-potential")?.addEventListener("change", (event) => {
        location.hash = hashFor({ ...state, potential: event.target.checked }, state.selected);
    });
}
function bind(view, state, page) {
    const navigate = (changes, selected = changes.selected ?? state.selected) => {
        location.hash = hashFor({ ...state, ...changes, offset: changes.offset ?? state.offset }, selected);
    };
    view.querySelector("#entity-explorer-form")?.addEventListener("submit", (event) => {
        event.preventDefault();
        const query = view.querySelector("#entity-explorer-query")?.value ?? "";
        const partyType = (view.querySelector("#entity-explorer-party")?.value ?? "");
        const minRecords = Math.max(0, Number(view.querySelector("#entity-explorer-min")?.value) || 0);
        const sort = query && !state.query ? "relevance" : query ? state.sort : "records";
        navigate({ query, partyType, minRecords, sort, selected: "", offset: 0, cursor: "" });
    });
    view.querySelector("#entity-explorer-size")?.addEventListener("change", (event) => navigate({ size: Number(event.target.value), offset: 0, cursor: "" }));
    view.querySelector("#entity-explorer-sort")?.addEventListener("change", (event) => navigate({ sort: event.target.value, offset: 0, cursor: "" }));
    view.querySelector("#entity-explorer-prev")?.addEventListener("click", () => {
        if (state.cursor) {
            history.back();
            return;
        }
        navigate({ offset: Math.max(0, state.offset - state.size), cursor: "" });
    });
    view.querySelector("#entity-explorer-next")?.addEventListener("click", () => {
        if (page.next_cursor)
            navigate({ offset: state.offset + state.size, cursor: page.next_cursor });
    });
    view.querySelectorAll("[data-entity-select]").forEach((row) => row.addEventListener("click", () => navigate({}, row.dataset.entitySelect ?? "")));
    bindPotential(view, state);
}
function replaceDetail(view, content, state) {
    const current = view.querySelector(".entity-explorer-detail");
    if (!current)
        return;
    const holder = document.createElement("div");
    holder.innerHTML = content;
    const replacement = holder.firstElementChild;
    if (!replacement)
        return;
    current.replaceWith(replacement);
    bindPotential(view, state);
}
const requestVersions = new WeakMap();
export async function mountEntityExplorer(el, port, store, scope) {
    const state = stateFromUrl();
    const version = (requestVersions.get(el) ?? 0) + 1;
    requestVersions.set(el, version);
    let mounted = true;
    el.addEventListener("unmount", () => { mounted = false; }, { once: true });
    const isCurrent = () => mounted && requestVersions.get(el) === version;
    el.innerHTML = renderLoading(state, store);
    bind(el, state, loadingPage(state));
    const page = await port.entityExplorer(state.query, state.offset, state.size, state.sort, filtersFrom(state), state.cursor || undefined);
    if (!isCurrent())
        return;
    const selected = state.selected || page.rows[0]?.nayose_id || "";
    const effectiveState = { ...state, selected };
    el.innerHTML = `<div class="panel entity-explorer">${renderSearch(page, effectiveState, store)}<div class="entity-explorer-layout" data-panel-split="entity-explorer">${renderResults(page, effectiveState, store)}<div class="panel-resizer" data-panel-resizer="entity-explorer" role="separator" aria-orientation="vertical" aria-label="${esc(store.t("resizePanel"))}" aria-valuemin="${ENTITY_SPLIT_MIN}" aria-valuemax="${ENTITY_SPLIT_MAX}" aria-valuenow="${ENTITY_SPLIT_DEFAULT}" tabindex="0"></div>${renderDetail(null, null, effectiveState, store)}</div></div>`;
    scope.add(bindPanelSplit(el, store, {
        id: "entity-explorer",
        cssVariable: "--entity-results-width",
        defaultWidth: ENTITY_SPLIT_DEFAULT,
        minWidth: ENTITY_SPLIT_MIN,
        maxWidth: ENTITY_SPLIT_MAX,
        pointerSide: "left",
        mobileQuery: "(max-width: 980px)",
    }));
    bind(el, effectiveState, page);
    if (!selected)
        return;
    try {
        const [detail, graph] = await Promise.all([port.entity(selected, 0, 10), port.entityGraph(selected, state.potential)]);
        if (!isCurrent())
            return;
        replaceDetail(el, renderDetail(detail, graph, effectiveState, store), effectiveState);
    }
    catch {
        if (!isCurrent())
            return;
        replaceDetail(el, `<aside class="card entity-explorer-detail"><p class="members" role="alert">${esc(store.t("detailError"))}</p></aside>`, effectiveState);
    }
}
