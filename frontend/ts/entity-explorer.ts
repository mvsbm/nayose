import type {
  EntityDetail,
  EntityExplorerPage,
  EntityExplorerPort,
  EntityGraph,
  EntityDetailPort,
  EntitySort,
  SearchFilters,
} from "./port.js";
import type { Store } from "./store.js";
import { asNumber } from "./core.js";
import { esc } from "./schema.js";
import { presentError } from "./api-error.js";
import { bindPanelSplit } from "./panel-layout.js";
import type { ViewScope } from "./lifecycle.js";

const ENTITY_SPLIT_DEFAULT = 520;
const ENTITY_SPLIT_MIN = 320;
const ENTITY_SPLIT_MAX = 800;

const PAGE_SIZES = [25, 50, 100, 200] as const;
const SORTS: EntitySort[] = ["relevance", "records", "name", "last_seen"];
type ExplorerPort = EntityExplorerPort & EntityDetailPort;

interface ExplorerState {
  query: string;
  offset: number;
  cursor: string;
  cursorHistory: string[];
  size: number;
  sort: EntitySort;
  partyType: "" | "person" | "company";
  minRecords: number;
  selected: string;
  potential: boolean;
}

function stateFromUrl(): ExplorerState {
  const params = new URLSearchParams(location.hash.split("?")[1] ?? "");
  const requestedSize = Number(params.get("size"));
  const requestedSort = params.get("sort") as EntitySort;
  const requestedParty = params.get("party_type");
  let cursorHistory: string[] = [];
  try {
    const parsed = JSON.parse(params.get("cursor_history") ?? "[]") as unknown;
    cursorHistory = Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string").slice(-100) : [];
  } catch {
    cursorHistory = [];
  }
  const partyType = requestedParty === "person" || requestedParty === "company" ? requestedParty : "";
  return {
    query: params.get("q") ?? "",
    offset: Math.max(0, Math.floor(Number(params.get("offset")) || 0)),
    cursor: params.get("cursor") ?? "",
    cursorHistory,
    size: PAGE_SIZES.includes(requestedSize as (typeof PAGE_SIZES)[number]) ? requestedSize : 50,
    sort: SORTS.includes(requestedSort) ? requestedSort : "records",
    partyType,
    minRecords: Math.max(0, Number(params.get("min_records")) || 0),
    selected: params.get("selected") ?? "",
    potential: params.get("potential") === "1",
  };
}

function filtersFrom(state: ExplorerState): SearchFilters {
  return {
    ...(state.partyType ? { party_type: state.partyType } : {}),
    ...(state.minRecords ? { min_record_count: state.minRecords } : {}),
  };
}

function hashFor(state: ExplorerState, selected = state.selected): string {
  const params = new URLSearchParams();
  if (state.query) params.set("q", state.query);
  if (state.offset) params.set("offset", String(state.offset));
  if (state.cursor) params.set("cursor", state.cursor);
  if (state.cursorHistory.length) params.set("cursor_history", JSON.stringify(state.cursorHistory));
  if (state.size !== 50) params.set("size", String(state.size));
  if (state.sort !== "records") params.set("sort", state.sort);
  if (state.partyType) params.set("party_type", state.partyType);
  if (state.minRecords) params.set("min_records", String(state.minRecords));
  if (selected) params.set("selected", selected);
  if (state.potential) params.set("potential", "1");
  const query = params.toString();
  return `#/entities${query ? `?${query}` : ""}`;
}

function display(value: unknown, missing: string): string {
  const text = value == null ? "" : String(value);
  return text || missing;
}

function resultRows(page: EntityExplorerPage, state: ExplorerState, store: Store): string {
  return page.rows.map((row) => `<tr class="${row.nayose_id === state.selected ? "selected" : ""}" data-entity-select="${esc(row.nayose_id)}">
    <th scope="row"><button type="button" class="button button-quiet entity-explorer-select">${esc(row.name_kanji || row.nayose_id)}</button><br><span class="text-meta">${esc(row.nayose_id)}</span></th>
    <td>${esc(row.name_kana || "")}</td><td>${asNumber(row.record_count)}</td>
    <td>${esc(row.party_type || "")}</td><td>${esc(row.last_seen || "")}</td>
    <td><a href="#/entity/${encodeURIComponent(row.nayose_id)}">${esc(store.t("inspectEntity"))}</a></td></tr>`).join("");
}

function renderSearch(page: EntityExplorerPage, state: ExplorerState, store: Store): string {
  const first = page.rows.length ? state.offset + 1 : 0;
  const last = state.offset + page.rows.length;
  const total = page.total_is_exact ? String(page.total) : page.total >= 0 ? `${page.total}+` : "?";
  const hasMore = page.has_more ?? (page.total_is_exact ? last < page.total : Boolean(page.next_cursor));
  return `<section class="card entity-explorer-search">
    <div class="toolbar"><div><p class="eyebrow">${esc(store.t("entityExplorerEyebrow"))}</p><h2>${esc(store.t("entityExplorerTitle"))}</h2></div>
      <span class="text-meta" role="status" aria-live="polite">${esc(store.t("entityExplorerRange", { first, last, total }))}</span></div>
    <p>${esc(store.t("entityExplorerHelp"))}</p>
    <form id="entity-explorer-form" class="entity-explorer-form">
      <label class="entity-explorer-query">${esc(store.t("explorerSearch"))}<input id="entity-explorer-query" type="search" value="${esc(state.query)}" placeholder="${esc(store.t("searchPlaceholder"))}"></label>
      <label>${esc(store.t("party_type"))}<select id="entity-explorer-party"><option value="">${esc(store.t("facetAll"))}</option><option value="person" ${state.partyType === "person" ? "selected" : ""}>${esc(store.t("facetPerson"))}</option><option value="company" ${state.partyType === "company" ? "selected" : ""}>${esc(store.t("facetCompany"))}</option></select></label>
      <label>${esc(store.t("minRecords"))}<input id="entity-explorer-min" type="number" min="0" step="1" value="${state.minRecords || ""}"></label>
      <button class="button button-secondary" type="submit">${esc(store.t("search"))}</button>
    </form>
    <div class="toolbar entity-explorer-controls"><label>${esc(store.t("pageSize"))}<select id="entity-explorer-size">${PAGE_SIZES.map((size) => `<option value="${size}" ${size === state.size ? "selected" : ""}>${size}</option>`).join("")}</select></label>
      <label>${esc(store.t("sort"))}<select id="entity-explorer-sort"><option value="records" ${state.sort === "records" ? "selected" : ""}>${esc(store.t("sortRecords"))}</option><option value="name" ${state.sort === "name" ? "selected" : ""}>${esc(store.t("sortName"))}</option><option value="last_seen" ${state.sort === "last_seen" ? "selected" : ""}>${esc(store.t("sortLastSeen"))}</option><option value="relevance" ${state.sort === "relevance" ? "selected" : ""}>${esc(store.t("sortRelevance"))}</option></select></label>
      <button type="button" class="button button-secondary" id="entity-explorer-prev" ${state.offset <= 0 ? "disabled" : ""}>${esc(store.t("previous"))}</button><button type="button" class="button button-secondary" id="entity-explorer-next" ${!hasMore || !page.next_cursor ? "disabled" : ""}>${esc(store.t("next"))}</button>
    </div>
  </section>`;
}

function renderResults(page: EntityExplorerPage, state: ExplorerState, store: Store): string {
  return `<section class="card viewport entity-explorer-results" aria-labelledby="entity-explorer-results-title">
    <div class="toolbar"><h3 id="entity-explorer-results-title">${esc(store.t("explorerResults"))}</h3><span class="text-meta">${esc(store.t(page.search_mode === "hybrid_fallback" ? "hybridFallback" : "exactIndex"))}</span></div>
    <div class="table-scroll" tabindex="0" role="region" aria-label="${esc(store.t("explorerResults"))}"><table><caption class="text-meta">${esc(store.t("entityExplorerTableHelp"))}</caption><thead><tr><th scope="col">${esc(store.t("name_kanji"))}</th><th scope="col">${esc(store.t("name_kana"))}</th><th scope="col">${esc(store.t("record_count"))}</th><th scope="col">${esc(store.t("party_type"))}</th><th scope="col">${esc(store.t("last_seen"))}</th><th scope="col">${esc(store.t("action"))}</th></tr></thead><tbody>${resultRows(page, state, store) || `<tr><td colspan="6" class="text-meta">${esc(store.t("none"))}</td></tr>`}</tbody></table></div>
  </section>`;
}

function loadingPage(state: ExplorerState): EntityExplorerPage {
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

function renderLoading(state: ExplorerState, store: Store): string {
  return `<div class="panel entity-explorer">${renderSearch(loadingPage(state), state, store)}<div class="entity-explorer-layout"><section class="card"><p role="status" aria-live="polite">${esc(store.t("entityExplorerLoading"))}</p><div class="skel skel-detail"><div class="card"></div></div></section><aside class="card viewport entity-explorer-detail"><p role="status" aria-live="polite">${esc(store.t("detailLoading"))}</p></aside></div></div>`;
}

type FootprintKind = "record" | "policy" | "agency" | "channel";

interface FootprintGroup {
  kind: FootprintKind;
  titleKey: string;
  nodes: EntityGraph["nodes"];
  count: number;
}

const FOOTPRINT_GROUPS: Array<{ kind: FootprintKind; titleKey: string }> = [
  { kind: "record", titleKey: "sourceRecords" },
  { kind: "policy", titleKey: "policies" },
  { kind: "agency", titleKey: "agencies" },
  { kind: "channel", titleKey: "channels" },
];

function nodeLabels(graph: EntityGraph): Map<string, string> {
  return new Map(graph.nodes.map((node) => [node.id, node.label]));
}

function observedNodes(graph: EntityGraph, kind: FootprintKind): EntityGraph["nodes"] {
  const connected = new Set<string>();
  graph.edges.filter((edge) => edge.kind === "observed").forEach((edge) => {
    connected.add(edge.source);
    connected.add(edge.target);
  });
  return graph.nodes.filter((node) => node.kind === kind && connected.has(node.id));
}

function footprintGroups(detail: EntityDetail, graph: EntityGraph): FootprintGroup[] {
  return FOOTPRINT_GROUPS.map(({ kind, titleKey }) => {
    const nodes = observedNodes(graph, kind);
    const field = kind === "record" ? "record_count" : kind === "policy" ? "policy_count" : kind === "agency" ? "agency_count" : "";
    const reported = field && typeof detail.entity[field] === "number" ? Number(detail.entity[field]) : nodes.length;
    return { kind, titleKey, nodes, count: Math.max(0, reported) };
  });
}

function previewLabels(group: FootprintGroup, store: Store): string {
  const labels = group.nodes.slice(0, 3).map((node) => node.label || node.id);
  const remainder = Math.max(0, group.count - labels.length);
  if (remainder) labels.push(store.t("moreItems", { n: remainder }));
  return labels.join(" · ") || store.t("none");
}

function renderOverview(detail: EntityDetail, groups: FootprintGroup[], state: ExplorerState, store: Store): string {
  const entity = detail.entity;
  const firstSeen = display(entity.first_seen, store.t("missingValue"));
  const lastSeen = display(entity.last_seen, store.t("missingValue"));
  return `<section class="entity-explorer-overview" aria-labelledby="entity-explorer-detail-title"><div class="entity-explorer-header"><div><p class="eyebrow">${esc(store.t("entityOverview"))}</p><h2 id="entity-explorer-detail-title">${esc(display(entity.name_kanji, state.selected))}</h2><p class="text-meta">${esc(state.selected)} · ${esc(display(entity.party_type, store.t("missingValue")))}</p></div><span class="chip-state">${esc(store.t("selectedEntity"))}</span></div><p class="text-meta">${esc(store.t("entitySummaryDisclaimer"))}</p><div class="entity-explorer-kpis" aria-label="${esc(store.t("entityCoverage"))}">${groups.map((group) => `<div class="entity-explorer-kpi"><span>${esc(store.t(group.titleKey))}</span><strong>${group.count}</strong></div>`).join("")}</div><dl class="entity-explorer-dates"><div><dt>${esc(store.t("first_seen"))}</dt><dd>${esc(firstSeen)}</dd></div><div><dt>${esc(store.t("last_seen"))}</dt><dd>${esc(lastSeen)}</dd></div></dl><a class="action-link" href="#/entity/${encodeURIComponent(state.selected)}">${esc(store.t("openEntityDetail"))}</a></section>`;
}

function renderFootprint(groups: FootprintGroup[], store: Store): string {
  return `<section id="entity-explorer-footprint" class="entity-explorer-footprint" aria-labelledby="entity-explorer-footprint-title"><div class="toolbar"><div><h3 id="entity-explorer-footprint-title">${esc(store.t("entityCoverage"))}</h3><p class="text-meta">${esc(store.t("entityCoverageHelp"))}</p></div><span class="text-meta">${esc(store.t("observedRelationship"))}</span></div><div class="entity-explorer-footprint-grid" role="list">${groups.map((group) => `<div class="entity-explorer-footprint-group" data-footprint-kind="${group.kind}" role="listitem"><div class="entity-explorer-footprint-heading"><span>${esc(store.t(group.titleKey))}</span><strong>${group.count}</strong></div><p class="text-meta">${esc(previewLabels(group, store))}</p></div>`).join("")}</div></section>`;
}

const RELATION_LABELS: Record<string, string> = {
  member_record: "sourceRecords",
  observed_policy: "policies",
  observed_agency: "agencies",
  source_channel: "channels",
  potential_match: "potentialMatches",
};

function relationLabel(edge: EntityGraph["edges"][number], store: Store): string {
  return store.t(RELATION_LABELS[edge.relation] ?? "observedLinks");
}

function evidenceText(edge: EntityGraph["edges"][number], store: Store): string {
  if (edge.kind === "potential") {
    const left = String(edge.evidence.left_record_id || store.t("missingValue"));
    const right = String(edge.evidence.right_record_id || store.t("missingValue"));
    const probability = edge.evidence.pair_probability;
    const score = typeof probability === "number" ? ` · ${(probability * 100).toFixed(1)}%` : "";
    return `${store.t("evidenceReviewQueue", { left, right })}${score}`;
  }
  return store.t("evidenceSourceRecord", { id: String(edge.evidence.record_id || store.t("missingValue")) });
}

function renderEvidenceTable(graph: EntityGraph, store: Store, kind: "observed" | "potential"): string {
  const labels = nodeLabels(graph);
  const edges = graph.edges.filter((edge) => edge.kind === kind);
  const rows = edges.map((edge) => `<tr><td><span class="relationship-badge ${edge.kind}">${esc(store.t(edge.kind === "potential" ? "potentialRelationship" : "observedRelationship"))}</span><br><span class="text-meta">${esc(relationLabel(edge, store))}</span></td><td>${esc(labels.get(edge.target) || edge.target)}</td><td>${esc(evidenceText(edge, store))}</td></tr>`).join("");
  return `<div class="table-scroll entity-explorer-evidence-table" tabindex="0" role="region" aria-label="${esc(store.t("evidenceTable"))}"><table><caption class="text-meta">${esc(store.t("evidenceTableHelp"))}</caption><thead><tr><th scope="col">${esc(store.t("relationType"))}</th><th scope="col">${esc(store.t("connectedItem"))}</th><th scope="col">${esc(store.t("evidence"))}</th></tr></thead><tbody>${rows || `<tr><td colspan="3" class="text-meta">${esc(store.t("none"))}</td></tr>`}</tbody></table></div>`;
}

function renderObservedEvidence(graph: EntityGraph, store: Store): string {
  const count = graph.edges.filter((edge) => edge.kind === "observed").length;
  return `<details id="entity-explorer-observed" class="entity-explorer-disclosure"><summary><span>${esc(store.t("observedLinks"))}</span><span class="entity-explorer-disclosure-meta">${esc(store.t("count", { n: count }))}</span></summary><p class="text-meta">${esc(store.t("observedEvidence"))}</p>${renderEvidenceTable(graph, store, "observed")}</details>`;
}

function renderPotentialEvidence(graph: EntityGraph, state: ExplorerState, store: Store): string {
  const count = graph.edges.filter((edge) => edge.kind === "potential").length;
  const body = graph.potential_matches_available
    ? `<label class="entity-explorer-potential"><input id="entity-explorer-potential" type="checkbox" ${state.potential ? "checked" : ""}>${esc(store.t("includePotential"))}</label>${state.potential ? renderEvidenceTable(graph, store, "potential") : ""}`
    : `<p class="text-meta">${esc(store.t("potentialUnavailable"))}</p>`;
  return `<details id="entity-explorer-potential-section" class="entity-explorer-disclosure" ${state.potential ? "open" : ""}><summary><span>${esc(store.t("potentialMatches"))}</span><span class="entity-explorer-disclosure-meta">${esc(state.potential ? store.t("count", { n: count }) : store.t("notShown"))}</span></summary><p class="text-meta">${esc(store.t("potentialMatchesHelp"))}</p>${body}</details>`;
}

function renderDetail(detail: EntityDetail | null, graph: EntityGraph | null, state: ExplorerState, store: Store): string {
  if (!detail || !graph) return `<aside class="card viewport entity-explorer-detail"><p class="text-meta" role="status" aria-live="polite">${esc(state.selected ? store.t("detailLoading") : store.t("selectEntity"))}</p></aside>`;
  const groups = footprintGroups(detail, graph);
  return `<aside class="card viewport entity-explorer-detail" data-selected-entity="${esc(state.selected)}">${renderOverview(detail, groups, state, store)}${renderFootprint(groups, store)}${graph.truncated ? `<p class="notice">${esc(store.t("graphTruncated"))}</p>` : ""}${renderObservedEvidence(graph, store)}${renderPotentialEvidence(graph, state, store)}</aside>`;
}

function bindPotential(view: HTMLElement, state: ExplorerState): void {
  view.querySelector("#entity-explorer-potential")?.addEventListener("change", (event) => {
    location.hash = hashFor({ ...state, potential: (event.target as HTMLInputElement).checked }, state.selected);
  });
}

function bind(view: HTMLElement, state: ExplorerState, page: EntityExplorerPage): void {
  const navigate = (changes: Partial<ExplorerState>, selected = changes.selected ?? state.selected): void => {
    location.hash = hashFor({ ...state, ...changes, offset: changes.offset ?? state.offset }, selected);
  };
  view.querySelector("#entity-explorer-form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const query = view.querySelector<HTMLInputElement>("#entity-explorer-query")?.value ?? "";
    const partyType = (view.querySelector<HTMLSelectElement>("#entity-explorer-party")?.value ?? "") as ExplorerState["partyType"];
    const minRecords = Math.max(0, Number(view.querySelector<HTMLInputElement>("#entity-explorer-min")?.value) || 0);
    const sort = query && !state.query ? "relevance" : query ? state.sort : "records";
    navigate({ query, partyType, minRecords, sort, selected: "", offset: 0, cursor: "" });
  });
  view.querySelector("#entity-explorer-size")?.addEventListener("change", (event) => navigate({ size: Number((event.target as HTMLSelectElement).value), offset: 0, cursor: "" }));
  view.querySelector("#entity-explorer-sort")?.addEventListener("change", (event) => navigate({ sort: (event.target as HTMLSelectElement).value as EntitySort, offset: 0, cursor: "" }));
  view.querySelector("#entity-explorer-prev")?.addEventListener("click", () => {
    const history = [...state.cursorHistory];
    const cursor = history.pop() ?? "";
    navigate({ offset: Math.max(0, state.offset - state.size), cursor, cursorHistory: history });
  });
  view.querySelector("#entity-explorer-next")?.addEventListener("click", () => {
    if (page.next_cursor) navigate({ offset: state.offset + state.size, cursor: page.next_cursor, cursorHistory: [...state.cursorHistory, state.cursor].slice(-100) });
  });
  view.querySelectorAll<HTMLElement>("[data-entity-select]").forEach((row) => row.addEventListener("click", (event) => {
    if ((event.target as HTMLElement).closest("a,input,details,summary")) return;
    navigate({}, row.dataset.entitySelect ?? "");
  }));
  bindPotential(view, state);
}

function replaceDetail(view: HTMLElement, content: string, state: ExplorerState): void {
  const current = view.querySelector(".entity-explorer-detail");
  if (!current) return;
  const holder = document.createElement("div");
  holder.innerHTML = content;
  const replacement = holder.firstElementChild;
  if (!replacement) return;
  current.replaceWith(replacement);
  bindPotential(view, state);
}

const requestVersions = new WeakMap<HTMLElement, number>();

export async function mountEntityExplorer(el: HTMLElement, port: ExplorerPort, store: Store, scope: ViewScope): Promise<void> {
  const state = stateFromUrl();
  const version = (requestVersions.get(el) ?? 0) + 1;
  requestVersions.set(el, version);
  let mounted = true;
  el.addEventListener("unmount", () => { mounted = false; }, { once: true });
  const isCurrent = (): boolean => mounted && requestVersions.get(el) === version;
  el.innerHTML = renderLoading(state, store);
  bind(el, state, loadingPage(state));
  const page = await port.entityExplorer(state.query, state.offset, state.size, state.sort, filtersFrom(state), state.cursor || undefined, scope.signal);
  if (!isCurrent()) return;
  const selected = state.selected || page.rows[0]?.nayose_id || "";
  const effectiveState = { ...state, selected };
  el.innerHTML = `<div class="panel entity-explorer">${renderSearch(page, effectiveState, store)}<div class="entity-explorer-layout" data-panel-split="entity-explorer">${renderResults(page, effectiveState, store)}<div class="panel-resizer sash" data-panel-resizer="entity-explorer" role="separator" aria-orientation="vertical" aria-label="${esc(store.t("resizePanel"))}" aria-valuemin="${ENTITY_SPLIT_MIN}" aria-valuemax="${ENTITY_SPLIT_MAX}" aria-valuenow="${ENTITY_SPLIT_DEFAULT}" tabindex="0"></div>${renderDetail(null, null, effectiveState, store)}</div></div>`;
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
  if (!selected) return;
  try {
    const [detail, graph] = await Promise.all([port.entity(selected, 0, 10, scope.signal), port.entityGraph(selected, state.potential, scope.signal)]);
    if (!isCurrent()) return;
    replaceDetail(el, renderDetail(detail, graph, effectiveState, store), effectiveState);
  } catch (error) {
    if (!isCurrent()) return;
    replaceDetail(el, `<aside class="card viewport entity-explorer-detail"><p class="text-meta" role="alert">${esc(store.t("detailError"))}: ${esc(presentError(error, store.t.bind(store)))}</p></aside>`, effectiveState);
  }
}
