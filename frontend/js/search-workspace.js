import { esc } from "./schema.js";
import { FIELD_GROUPS, recordFields, value } from "./evidence-view.js";
export function createSearchDetailState() {
    return { selectedId: "", detail: null, pending: false, error: "", requestId: 0 };
}
export function renderSearchDetail(state, store) {
    const t = (key) => esc(store.t(key));
    if (state.pending)
        return `<div class="search-workspace-empty" role="status" aria-busy="true"><p>${t("detailLoading")}</p></div>`;
    if (state.error)
        return `<div class="search-workspace-empty" role="alert"><p>${esc(state.error)}</p></div>`;
    if (!state.selectedId || !state.detail)
        return `<div class="search-workspace-empty"><h2>${t("searchDetail")}</h2><p>${t("selectResult")}</p></div>`;
    const entity = state.detail.entity;
    const members = state.detail.members;
    const memberRange = esc(store.t("memberRange", { first: members.length ? 1 : 0, last: members.length, total: state.detail.total }));
    return `<div class="search-workspace-detail-shell" data-selected-entity="${esc(state.selectedId)}"><header class="search-workspace-detail-header"><div><p class="search-workspace-detail-eyebrow">${t("searchDetail")}</p><h2>${value(store, entity.name_kanji)} <span class="muted">${value(store, entity.name_kana)}</span></h2><p class="members">${t("nayose_id")}: ${esc(state.selectedId)}</p></div><button id="search-detail-close" class="search-workspace-detail-close" type="button" aria-label="${t("closeDetail")}">×</button></header><div class="search-workspace-detail-meta"><span><b>${value(store, entity.record_count)}</b> ${t("record_count")}</span><span><b>${value(store, entity.policy_count)}</b> ${t("policy_count")}</span><span><b>${value(store, entity.agency_count)}</b> ${t("agency_count")}</span></div><section class="search-workspace-detail-section"><div class="search-workspace-detail-section-heading"><h3>${t("canonicalTitle")}</h3><span class="members">${t("canonicalHelp")}</span></div>${recordFields(store, { ...entity, nayose_id: state.selectedId }, ["nayose_id", "record_count", "policy_count", "agency_count", "first_seen", "last_seen"])}<details class="search-workspace-detail-more"><summary>${t("fullRecord")}</summary>${recordFields(store, entity, [...FIELD_GROUPS.identity, ...FIELD_GROUPS.contact])}</details></section><details class="search-workspace-detail-members"><summary><span>${t("members")}</span><span class="members">${memberRange}</span></summary><p class="members">${t("sourceHelp")}</p><div class="table-scroll" tabindex="0" role="region" aria-label="${t("members")}"><table><thead><tr><th scope="col">${t("record_id")}</th><th scope="col">${t("identity")}</th><th scope="col">${t("contact")}</th><th scope="col">${t("source")}</th></tr></thead><tbody>${members.map(member => `<tr><th scope="row">${value(store, member.record_id)}</th><td>${value(store, member.name_kanji)}<br>${value(store, member.name_kana)}<br>${value(store, member.date_birth)}</td><td>${value(store, member.phone)}<br>${value(store, member.address)}</td><td>${value(store, member.channel)}<br>${value(store, member.observed_at)}</td></tr>`).join("") || `<tr><td colspan="4">${t("none")}</td></tr>`}</tbody></table></div></details></div>`;
}
export function bindSearchDetail(host, onClose) {
    host.querySelector("#search-detail-close")?.addEventListener("click", onClose);
}
export async function loadSearchDetail(host, state, entityId, port, store, paint) {
    const requestId = ++state.requestId;
    state.selectedId = entityId;
    state.detail = null;
    state.pending = true;
    state.error = "";
    paint();
    try {
        const detail = await port.entity(entityId, 0, 10);
        if (requestId !== state.requestId)
            return;
        state.detail = detail;
    }
    catch (error) {
        if (requestId !== state.requestId)
            return;
        state.error = `${store.t("detailError")}: ${String(error.message ?? error)}`;
    }
    finally {
        if (requestId !== state.requestId)
            return;
        state.pending = false;
        paint();
    }
}
export function clearSearchDetail(state, paint) {
    state.requestId += 1;
    state.selectedId = "";
    state.detail = null;
    state.pending = false;
    state.error = "";
    paint();
}
