import { esc } from "./schema.js";
import { presentError } from "./api-error.js";
import { FIELD_GROUPS, recordFields, value } from "./evidence-view.js";
import { asyncError, asyncIdle, asyncLoading, asyncReady } from "./async-state.js";
export function createSearchDetailState() {
    return { selectedId: "", request: asyncIdle(), requestId: 0, controller: null };
}
function renderDetailHeader(state, detail, store, t) {
    const entity = detail.entity;
    return `<header class="search-workspace-detail-header"><div><p class="search-workspace-detail-eyebrow">${t("searchDetail")}</p><h2>${value(store, entity.name_kanji)} <span class="muted">${value(store, entity.name_kana)}</span></h2><p class="members">${t("nayose_id")}: ${esc(state.selectedId)}</p></div><button id="search-detail-close" class="search-workspace-detail-close" type="button" aria-label="${t("closeDetail")}">×</button></header>`;
}
function renderCanonical(detail, state, store, t) {
    return `<section class="search-workspace-detail-section"><div class="search-workspace-detail-section-heading"><h3>${t("canonicalTitle")}</h3><span class="members">${t("canonicalHelp")}</span></div>${recordFields(store, { ...detail.entity, nayose_id: state.selectedId }, ["nayose_id", "record_count", "policy_count", "agency_count", "first_seen", "last_seen"])}<details class="search-workspace-detail-more"><summary>${t("fullRecord")}</summary>${recordFields(store, detail.entity, [...FIELD_GROUPS.identity, ...FIELD_GROUPS.contact])}</details></section>`;
}
function renderMembers(detail, store, t) {
    const first = detail.members.length ? detail.offset + 1 : 0;
    const last = detail.offset + detail.members.length;
    const memberRange = esc(store.t("memberRange", { first, last, total: detail.total }));
    const rows = detail.members.map(member => `<tr><th scope="row">${value(store, member.record_id)}</th><td>${value(store, member.name_kanji)}<br>${value(store, member.name_kana)}<br>${value(store, member.date_birth)}</td><td>${value(store, member.phone)}<br>${value(store, member.address)}</td><td>${value(store, member.channel)}<br>${value(store, member.observed_at)}</td></tr>`).join("") || `<tr><td colspan="4">${t("none")}</td></tr>`;
    const entityId = String(detail.entity.nayose_id ?? "");
    const entityLink = entityId ? `<a class="more" href="#/entity/${encodeURIComponent(entityId)}">${t("openEntityDetail")}</a>` : "";
    return `<details class="search-workspace-detail-members"><summary><span>${t("members")}</span><span class="members">${memberRange}</span></summary><p class="members">${t("sourceHelp")}</p>${entityLink}<div class="table-scroll" tabindex="0" role="region" aria-label="${t("members")}"><table><caption class="members">${t("members")}</caption><thead><tr><th scope="col">${t("record_id")}</th><th scope="col">${t("identity")}</th><th scope="col">${t("contact")}</th><th scope="col">${t("source")}</th></tr></thead><tbody>${rows}</tbody></table></div></details>`;
}
export function renderSearchDetail(state, store) {
    const t = (key) => esc(store.t(key));
    if (state.request.kind === "loading")
        return `<div class="search-workspace-empty" role="status" aria-busy="true"><p>${t("detailLoading")}</p></div>`;
    if (state.request.kind === "error")
        return `<div class="search-workspace-empty" role="alert"><p>${esc(state.request.message)}</p></div>`;
    if (!state.selectedId || state.request.kind !== "ready")
        return `<div class="search-workspace-empty"><h2>${t("searchDetail")}</h2><p>${t("selectResult")}</p></div>`;
    const detail = state.request.value;
    const entity = detail.entity;
    return `<div class="search-workspace-detail-shell" data-selected-entity="${esc(state.selectedId)}">${renderDetailHeader(state, detail, store, t)}<div class="search-workspace-detail-meta"><span><b>${value(store, entity.record_count)}</b> ${t("record_count")}</span><span><b>${value(store, entity.policy_count)}</b> ${t("policy_count")}</span><span><b>${value(store, entity.agency_count)}</b> ${t("agency_count")}</span></div>${renderCanonical(detail, state, store, t)}${renderMembers(detail, store, t)}</div>`;
}
export function bindSearchDetail(host, onClose) {
    host.querySelector("#search-detail-close")?.addEventListener("click", onClose);
}
export async function loadSearchDetail(host, state, entityId, port, store, paint, signal) {
    const requestId = ++state.requestId;
    state.controller?.abort();
    const controller = new AbortController();
    const abortExternal = () => controller.abort();
    if (signal?.aborted)
        controller.abort();
    else
        signal?.addEventListener("abort", abortExternal, { once: true });
    state.controller = controller;
    state.selectedId = entityId;
    state.request = asyncLoading(String(requestId));
    paint();
    try {
        const detail = await port.entity(entityId, 0, 10, controller.signal);
        if (requestId !== state.requestId)
            return;
        state.request = asyncReady(detail);
    }
    catch (error) {
        if (requestId !== state.requestId)
            return;
        if (controller.signal.aborted)
            return;
        state.request = asyncError(`${store.t("detailError")}: ${presentError(error, store.t.bind(store))}`, String(requestId));
    }
    finally {
        signal?.removeEventListener("abort", abortExternal);
        if (requestId !== state.requestId)
            return;
        state.controller = null;
        if (state.request.kind === "loading")
            state.request = asyncIdle();
        paint();
    }
}
export function clearSearchDetail(state, paint) {
    state.requestId += 1;
    state.controller?.abort();
    state.controller = null;
    state.selectedId = "";
    state.request = asyncIdle();
    paint();
}
