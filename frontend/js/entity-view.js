import { esc } from "./schema.js";
import { comparisonTable, FIELD_GROUPS, recordFields, value } from "./evidence-view.js";
export async function mountEntityDetail(el, port, store, id, scope) {
    const params = new URLSearchParams(location.hash.split("?")[1] ?? "");
    const requestedPage = Number(params.get("page")) || 1;
    const page = Math.max(1, Math.min(1000000, Math.floor(requestedPage)));
    const requestedSize = Number(params.get("size"));
    const size = [10, 25, 50, 100].includes(requestedSize) ? requestedSize : 25;
    const detail = await port.entity(id, (page - 1) * size, size, scope.signal);
    if (scope?.isDisposed)
        return;
    const t = (key) => esc(store.t(key));
    const { entity, members, total } = detail;
    const pageLink = (p, s = size) => `#/entity/${encodeURIComponent(id)}?page=${p}&size=${s}`;
    if (page > 1 && !members.length && total > 0) {
        location.hash = pageLink(Math.ceil(total / size));
        return;
    }
    el.innerHTML = `<div class="panel">
    <nav class="crumbs" aria-label="${t("crumbs")}"><a href="#/entities">${t("entities")}</a><span>${esc(id)}</span></nav>
    <section class="card" data-entity="${esc(id)}"><h2>${value(store, entity.name_kanji)} <span class="muted">${value(store, entity.name_kana)}</span></h2>
      <h3>${t("canonicalTitle")}</h3><p>${t("canonicalHelp")}</p>
      ${recordFields(store, { ...entity, nayose_id: id }, ["nayose_id", "record_count", "policy_count", "agency_count", "first_seen", "last_seen"])}
      <details><summary>${t("fullRecord")}</summary>${recordFields(store, entity, [...FIELD_GROUPS.identity, ...FIELD_GROUPS.contact])}</details>
      <p class="notice">${t("mergeUnknown")}</p></section>
    <section class="card"><h3>${t("members")}</h3><p>${t("sourceHelp")}</p>
      <p>${t("legend")}</p>
      <div class="toolbar"><label>${t("pageSize")} <select id="member-size">${[10, 25, 50, 100].map(n => `<option ${n === size ? "selected" : ""}>${n}</option>`).join("")}</select></label>
        <span id="member-count" role="status">${esc(store.t("memberRange", { first: members.length ? (page - 1) * size + 1 : 0, last: (page - 1) * size + members.length, total }))}</span>
        <button class="more" id="member-prev" ${page <= 1 ? "disabled" : ""}>${t("previous")}</button>
        <button class="more" id="member-next" ${(page * size >= total) ? "disabled" : ""}>${t("next")}</button></div>
      <div class="toolbar"><span>${t("selectCompare")}</span><span id="member-selected" role="status"></span><button id="member-compare" class="more" disabled>${t("compare")}</button><button id="member-clear" class="more">${t("clearSelection")}</button></div>
      <div class="table-scroll" tabindex="0" role="region" aria-label="${t("members")}"><table><caption class="members">${t("members")}</caption><thead><tr><th scope="col">${t("compare")}</th><th scope="col">${t("record_id")}</th><th scope="col">${t("identity")}</th><th scope="col">${t("contact")}</th><th scope="col">${t("source")}</th></tr></thead>
      <tbody>${members.map(m => {
        const rid = String(m.record_id);
        const comparisons = detail.comparisons[rid] ?? {};
        const conflicts = Object.entries(comparisons).filter(([, c]) => c.status === "different").map(([f]) => store.t(f));
        return `<tr data-member="${esc(rid)}"><td><input type="checkbox" data-select="${esc(rid)}" aria-label="${t("compare")} ${esc(rid)}"></td>
          <th scope="row">${esc(rid)}</th><td>${value(store, m.name_kanji)}<br>${value(store, m.name_kana)}<br>${value(store, m.date_birth)}</td>
          <td>${value(store, m.phone)}<br>${value(store, m.address)}${conflicts.length ? `<p class="notice">≠ ${t("different")}: ${esc(conflicts.join(", "))}</p>` : ""}</td>
          <td>${value(store, m.channel)}<br>${value(store, m.observed_at)}<br>${value(store, m.policy_id)} / ${value(store, m.agency_id)}</td></tr>
          <tr><td colspan="5"><details><summary>${t("fullRecord")} — ${esc(rid)}</summary>
          ${Object.entries(FIELD_GROUPS).map(([key, fields]) => `<h4>${t(key)}</h4>${recordFields(store, m, fields)}`).join("")}
          <p>${t("evidenceHelp")}</p>${comparisonTable(store, comparisons, "canonical")}</details></td></tr>`;
    }).join("") || `<tr><td colspan="5">${t("none")}</td></tr>`}</tbody></table></div></section></div>`;
    el.querySelector("#member-prev").addEventListener("click", () => { location.hash = pageLink(page - 1); });
    el.querySelector("#member-next").addEventListener("click", () => { location.hash = pageLink(page + 1); });
    el.querySelector("#member-size").addEventListener("change", e => { location.hash = pageLink(1, Number(e.target.value)); });
    const picked = new Set(store.entitySelection.id === id ? store.entitySelection.records : []);
    const checks = [...el.querySelectorAll("[data-select]")];
    const compare = el.querySelector("#member-compare");
    const updateSelection = () => {
        checks.forEach(c => { c.checked = picked.has(c.dataset.select); c.disabled = picked.size === 2 && !c.checked; });
        el.querySelector("#member-selected").textContent = `${store.t("selected", { n: picked.size })} ${[...picked].join(", ")}`;
        compare.disabled = picked.size !== 2;
    };
    checks.forEach(input => input.addEventListener("change", () => {
        const rid = input.dataset.select;
        if (input.checked)
            picked.add(rid);
        else
            picked.delete(rid);
        store.selectMember(id, rid, input.checked);
        updateSelection();
    }));
    el.querySelectorAll("[data-member]").forEach((row) => row.addEventListener("click", (event) => {
        const target = event.target;
        if (target.closest("input, a, button, details, summary"))
            return;
        const input = row.querySelector("[data-select]");
        if (!input || input.disabled)
            return;
        const view = row.ownerDocument.defaultView;
        if (!view)
            return;
        input.checked = !input.checked;
        input.dispatchEvent(new view.Event("change"));
    }));
    el.querySelector("#member-clear").addEventListener("click", () => {
        picked.forEach(rid => store.selectMember(id, rid, false));
        picked.clear();
        updateSelection();
    });
    updateSelection();
    compare.addEventListener("click", () => { if (picked.size === 2)
        location.hash = `#/compare/${[...picked].map(encodeURIComponent).join("/")}`; });
}
