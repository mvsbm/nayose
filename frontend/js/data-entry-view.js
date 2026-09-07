import { esc } from "./schema.js";
import { pollJob } from "./core.js";
const ENTRY_FIELDS = ["name_kanji", "name_kana", "name_romaji", "date_birth", "postal_code", "address", "phone", "email", "party_type"];
export async function mountDataEntry(el, port, store) {
    const t = (key) => esc(store.t(key));
    const recovery = (err) => `${store.t(String(err.message ?? err))} ${store.t("errorNextStep")}`;
    let notice = "";
    const input = (field) => `<label>${t(field)}<input class="bar" data-field="${field}" value=""></label>`;
    const refresh = async () => {
        const list = await port.manualList();
        const rows = list.rows
            .map(r => `<tr data-mid="${esc(String(r.record_id))}"><td>${esc(String(r.record_id))}</td><td>${esc(String(r.name_kanji))}</td><td>${esc(String(r.phone))}</td><td><button type="button" class="more undo-m">${t("undo")}</button></td></tr>`)
            .join("");
        el.innerHTML = `<div class="panel"><nav class="crumbs" aria-label="${t("crumbs")}">${t("dataEntry")}</nav>
    <section class="card"><h2>${t("entryTitle")}</h2><p>${t("entryHelp")}</p>
      <div class="field-grid">${ENTRY_FIELDS.map(input).join("")}</div>
      <div class="toolbar"><button id="de-check" class="more" type="button">${t("checkMatch")}</button>
        <button id="de-register" class="more" type="button" disabled>${t("register")}</button></div>
      <p id="de-status" role="status" aria-live="polite"></p>
      <div id="de-match" class="panel"></div></section>
    <section class="card"><h2>${t("importTitle")}</h2><p>${t("importHelp")}</p>
      <input type="file" id="de-file" accept=".csv,text/csv" aria-label="CSV">
      <div class="toolbar"><button id="de-dry" class="more" type="button" disabled>${t("dryRun")}</button>
        <button id="de-import" class="more" type="button" disabled>${t("confirmImport")}</button></div>
      <p id="de-import-status" role="status" aria-live="polite"></p>
      <div id="de-preview"></div></section>
    <section class="card"><h2>${t("manualList")} (${list.count})</h2>
      <div class="table-scroll" tabindex="0"><table><thead><tr><th>id</th><th>氏名</th><th>電話</th><th></th></tr></thead><tbody>${rows || `<tr><td colspan="4">${t("none")}</td></tr>`}</tbody></table></div></section>
    ${notice ? `<p class="notice" role="status">${esc(notice)}</p>` : ""}</div>`;
        const collect = () => Object.fromEntries([...el.querySelectorAll("[data-field]")].map(i => [i.dataset.field, i.value.trim()]));
        el.querySelector("#de-check")?.addEventListener("click", () => {
            const record = collect();
            if (!record.name_kanji) {
                el.querySelector("#de-status").textContent = store.t("nameRequired");
                return;
            }
            el.querySelector("#de-status").textContent = store.t("matching");
            const out = el.querySelector("#de-match");
            port.startRecommendation(record)
                .then(job => pollJob(() => port.recommendation(job.id), job))
                .then(job => {
                const r = job.result;
                el.querySelector("#de-register").removeAttribute("disabled");
                el.querySelector("#de-status").textContent = store.t("matchChecked");
                out.innerHTML = r
                    ? `<p>${t(r.action)} · ${Math.round((r.top_probability ?? 0) * 1000) / 10}% · ${esc(String(r.top_nayose_id ?? ""))}</p>`
                    : `<p>${store.t("matchFailed")} ${store.t("errorNextStep")}</p>`;
            })
                .catch(err => { el.querySelector("#de-status").textContent = recovery(err); });
        });
        el.querySelector("#de-register")?.addEventListener("click", () => {
            const btn = el.querySelector("#de-register");
            if (btn.disabled)
                return; // single-shot guard
            btn.disabled = true;
            const record = collect();
            port.manualRegister(record)
                .then(res => { notice = `${store.t("registered")} ${res.record_id}`; return refresh(); })
                .catch(err => { notice = recovery(err); btn.disabled = false; return refresh(); });
        });
        el.querySelectorAll(".undo-m").forEach(b => b.addEventListener("click", () => {
            const rid = b.closest("tr").dataset.mid ?? "";
            port.manualUndo([rid]).then(() => refresh());
        }));
        el.querySelector("#de-file")?.addEventListener("change", async (e) => {
            const file = e.target.files?.[0];
            if (!file)
                return;
            const text = await file.text();
            const rows = parseCsv(text).slice(0, 500);
            el.querySelector("#de-import").dataset.rows = JSON.stringify(rows);
            el.querySelector("#de-dry").disabled = rows.length === 0;
            el.querySelector("#de-import").disabled = rows.length === 0;
            el.querySelector("#de-preview").innerHTML = `<p class="members">${store.t("parsedRows", { n: rows.length })}</p>`;
        });
        const rowsFrom = () => JSON.parse((el.querySelector("#de-import").dataset.rows) ?? "[]");
        el.querySelector("#de-dry")?.addEventListener("click", () => {
            port.manualImport(rowsFrom(), true)
                .then(res => { el.querySelector("#de-import-status").textContent = `${store.t("previewStatus", { valid: String(res.valid ?? 0), errors: String((res.errors ?? []).length), dups: String((res.duplicates ?? []).length) })}`; })
                .catch(err => { el.querySelector("#de-import-status").textContent = recovery(err); });
        });
        el.querySelector("#de-import")?.addEventListener("click", () => {
            port.manualImport(rowsFrom(), false)
                .then(res => { notice = `${store.t("importedRows")} ${String(res.imported ?? 0)}`; return refresh(); })
                .catch(err => { notice = recovery(err); return refresh(); });
        });
    };
    await refresh();
}
// minimal CSV parse: header-less rows of name_kanji, name_kana, phone, email
function parseCsv(text) {
    return text.split(/\r?\n/).filter(Boolean).slice(0, 500).map(line => {
        const cols = line.split(",").map(c => c.trim());
        return { name_kanji: cols[0] ?? "", name_kana: cols[1] ?? "", phone: cols[2] ?? "", email: cols[3] ?? "" };
    }).filter(r => r.name_kanji);
}
