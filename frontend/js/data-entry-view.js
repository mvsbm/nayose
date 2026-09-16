import { presentError } from "./api-error.js";
import { esc } from "./schema.js";
import { pollJob } from "./core.js";
import { asyncError, asyncIdle, asyncLoading, asyncReady, isAsyncLoading } from "./async-state.js";
const ENTRY_FIELDS = ["name_kanji", "name_kana", "name_romaji", "date_birth", "postal_code", "address", "phone", "email", "party_type"];
function sameRecord(left, right) {
    return Boolean(left) && JSON.stringify(left) === JSON.stringify(right);
}
function translatedError(store, error) {
    return presentError(error, store.t.bind(store));
}
export async function mountDataEntry(el, port, store, viewScope) {
    el.innerHTML = `<div class="panel" role="status" aria-live="polite" aria-label="${esc(store.t("loading"))}"><div class="skel skel-tall"><div class="card"></div></div></div>`;
    const t = (key) => esc(store.t(key));
    const recovery = (err) => `${translatedError(store, err)} ${store.t("errorNextStep")}`;
    let notice = "";
    let matchRequest = asyncIdle();
    let importRequest = asyncIdle();
    const checking = () => isAsyncLoading(matchRequest);
    const importing = () => isAsyncLoading(importRequest);
    const input = (field) => `<label>${t(field)}<input class="bar" data-field="${field}" value="${esc(store.manualDraft[field] ?? "")}" autocomplete="${field === "name_kanji" ? "name" : field === "email" ? "email" : field === "phone" ? "tel" : "off"}"></label>`;
    const refresh = async () => {
        if (viewScope.isDisposed)
            return;
        let list;
        try {
            list = await port.manualList(viewScope.signal);
        }
        catch (error) {
            notice = recovery(error);
            if (!viewScope.isDisposed)
                el.innerHTML = `<div class="panel"><p class="notice" role="alert">${esc(notice)}</p><button id="de-retry" type="button" class="more">${t("retry")}</button></div>`;
            el.querySelector("#de-retry")?.addEventListener("click", () => void refresh());
            return;
        }
        if (viewScope.isDisposed)
            return;
        const rows = list.rows
            .map(r => `<tr data-mid="${esc(String(r.record_id))}"><th scope="row">${esc(String(r.record_id))}</th><td>${esc(String(r.name_kanji ?? ""))}</td><td>${esc(String(r.phone ?? ""))}</td><td><button type="button" class="more undo-m">${t("undo")}</button></td></tr>`)
            .join("");
        const currentRecord = () => Object.fromEntries([...el.querySelectorAll("[data-field]")].map(i => [i.dataset.field, i.value.trim()]));
        const registerReady = () => sameRecord(store.manualMatchRecord, currentRecord());
        el.innerHTML = `<div class="panel"><nav class="crumbs" aria-label="${t("crumbs")}">${t("dataEntry")}</nav>
    <section class="card"><h2>${t("entryTitle")}</h2><p>${t("entryHelp")}</p>
      <ol class="workflow-steps" aria-label="${t("entryProgress")}">
        <li data-entry-step="1"><span>1</span>${t("entryStepInput")}</li>
        <li data-entry-step="2"><span>2</span>${t("entryStepCheck")}</li>
        <li data-entry-step="3"><span>3</span>${t("entryStepRegister")}</li>
      </ol>
      <div class="field-grid">${ENTRY_FIELDS.map(input).join("")}</div>
      <div class="toolbar"><button id="de-check" class="more button-primary" type="button" ${checking() ? "disabled" : ""}>${t("checkMatch")}</button>
        <button id="de-register" class="more button-secondary" type="button" ${registerReady() ? "" : "disabled"}>${t("register")}</button></div>
      <p id="de-status" role="status" aria-live="polite">${esc(store.manualMatchSummary)}</p>
      <div id="de-match" class="panel"></div></section>
    <section class="card"><h2>${t("importTitle")}</h2><p>${t("importHelp")}</p>
      <label>${t("csvFile")}<input type="file" id="de-file" accept=".csv,text/csv" aria-label="${t("csvFile")}"></label>
      <div class="toolbar"><button id="de-dry" class="more button-secondary" type="button" ${!store.manualImportRows.length || importing() ? "disabled" : ""}>${t("dryRun")}</button>
        <button id="de-import" class="more button-primary" type="button" ${!store.manualImportRows.length || !store.manualImportReady || importing() ? "disabled" : ""}>${t("confirmImport")}</button></div>
      <p id="de-import-status" role="status" aria-live="polite"></p>
      <div id="de-preview">${store.manualImportRows.length ? `<p class="members">${esc(store.t("parsedRows", { n: store.manualImportRows.length }))}</p>` : ""}</div></section>
    <section class="card"><h2>${t("manualList")} (${esc(list.count)})</h2>
      <div class="table-scroll" tabindex="0" role="region" aria-label="${t("manualList")}"><table><caption class="members">${t("manualList")}</caption><thead><tr><th scope="col">${t("tableId")}</th><th scope="col">${t("tableName")}</th><th scope="col">${t("tablePhone")}</th><th scope="col">${t("action")}</th></tr></thead><tbody>${rows || `<tr><td colspan="4">${t("none")}</td></tr>`}</tbody></table></div></section>
    ${notice ? `<p class="notice" role="status">${esc(notice)}</p>` : ""}</div>`;
        const setEntryStep = (step) => {
            el.querySelectorAll("[data-entry-step]").forEach(item => {
                const itemStep = Number(item.dataset.entryStep);
                item.dataset.state = itemStep < step ? "complete" : itemStep === step ? "current" : "upcoming";
                if (itemStep === step)
                    item.setAttribute("aria-current", "step");
                else
                    item.removeAttribute("aria-current");
            });
        };
        const syncRegistration = () => {
            const button = el.querySelector("#de-register");
            if (button)
                button.disabled = checking() || !registerReady();
        };
        setEntryStep(sameRecord(store.manualMatchRecord, store.manualDraft) ? 3 : 1);
        el.querySelectorAll("[data-field]").forEach(field => field.addEventListener("input", () => {
            store.manualDraft = currentRecord();
            store.manualMatchRecord = null;
            store.manualMatchSummary = "";
            el.querySelector("#de-status").textContent = "";
            setEntryStep(1);
            syncRegistration();
        }));
        el.querySelector("#de-check")?.addEventListener("click", () => {
            if (checking())
                return;
            const record = currentRecord();
            store.manualDraft = record;
            if (!record.name_kanji) {
                el.querySelector("#de-status").textContent = store.t("nameRequired");
                return;
            }
            matchRequest = asyncLoading("recommendation");
            setEntryStep(2);
            syncRegistration();
            const output = el.querySelector("#de-match");
            const request = port.startRecommendation(record, undefined, viewScope.signal)
                .then(job => pollJob(() => port.recommendation(job.id, viewScope.signal), job, 300, current => {
                if (!viewScope.isDisposed)
                    el.querySelector("#de-status").textContent = store.t(current.stage);
            }, viewScope.signal));
            request.then(job => {
                if (viewScope.isDisposed)
                    return;
                if (job.poll_status !== "terminal" || job.stage !== "complete" || !job.result) {
                    store.manualMatchRecord = null;
                    store.manualMatchSummary = job.poll_status === "timeout" ? store.t("jobStillRunning") : store.t("matching_failed");
                    el.querySelector("#de-status").textContent = "";
                    output.innerHTML = `<div class="notice" role="alert"><strong>${t("matchFailed")}</strong><p>${esc(store.manualMatchSummary)}</p></div>`;
                }
                else {
                    store.manualMatchRecord = record;
                    store.manualMatchSummary = store.t("matchChecked");
                    const result = job.result;
                    output.innerHTML = `<p>${t(result.action)} · ${esc(Math.round((result.top_probability ?? 0) * 1000) / 10)}% · ${esc(String(result.top_nayose_id ?? ""))}</p>`;
                    el.querySelector("#de-status").textContent = store.manualMatchSummary;
                    setEntryStep(3);
                }
                syncRegistration();
            }).catch(error => {
                if (!viewScope.isDisposed) {
                    matchRequest = asyncError(recovery(error));
                    store.manualMatchRecord = null;
                    store.manualMatchSummary = recovery(error);
                    el.querySelector("#de-status").textContent = "";
                    output.innerHTML = `<div class="notice" role="alert"><strong>${t("matchFailed")}</strong><p>${esc(store.manualMatchSummary)}</p></div>`;
                    setEntryStep(2);
                    syncRegistration();
                }
            }).finally(() => { if (matchRequest.kind === "loading")
                matchRequest = asyncReady(undefined); syncRegistration(); });
        });
        el.querySelector("#de-register")?.addEventListener("click", () => {
            const btn = el.querySelector("#de-register");
            const record = currentRecord();
            if (btn.disabled || !registerReady())
                return;
            btn.disabled = true;
            setEntryStep(3);
            port.manualRegister(record, viewScope.signal)
                .then(res => {
                if (viewScope.isDisposed)
                    return;
                notice = `${store.t("registered")} ${res.record_id}`;
                store.manualDraft = {};
                store.manualMatchRecord = null;
                store.manualMatchSummary = "";
                return refresh();
            })
                .catch(err => { if (!viewScope.isDisposed) {
                notice = recovery(err);
                void refresh();
            } });
        });
        el.querySelectorAll(".undo-m").forEach(button => button.addEventListener("click", () => {
            const rid = button.closest("tr")?.getAttribute("data-mid") ?? "";
            if (!rid || button.disabled)
                return;
            button.disabled = true;
            port.manualUndo([rid], viewScope.signal).then(() => { if (!viewScope.isDisposed)
                void refresh(); }).catch(error => {
                if (!viewScope.isDisposed) {
                    notice = recovery(error);
                    void refresh();
                }
            });
        }));
        el.querySelector("#de-file")?.addEventListener("change", async (event) => {
            const file = event.target.files?.[0];
            if (!file)
                return;
            try {
                const rows = parseCsv(await file.text()).slice(0, 500);
                store.manualImportRows = rows;
                store.manualImportReady = false;
                el.querySelector("#de-dry").disabled = rows.length === 0;
                el.querySelector("#de-import").disabled = true;
                el.querySelector("#de-preview").innerHTML = `<p class="members">${esc(store.t("parsedRows", { n: rows.length }))}</p>`;
            }
            catch (error) {
                store.manualImportRows = [];
                store.manualImportReady = false;
                el.querySelector("#de-import-status").textContent = recovery(error);
            }
        });
        el.querySelector("#de-dry")?.addEventListener("click", () => {
            if (importing() || !store.manualImportRows.length)
                return;
            importRequest = asyncLoading("dry-run");
            const dry = el.querySelector("#de-dry");
            const confirm = el.querySelector("#de-import");
            dry.disabled = true;
            confirm.disabled = true;
            port.manualImport(store.manualImportRows, true, viewScope.signal)
                .then(res => {
                if (viewScope.isDisposed)
                    return;
                const errors = Array.isArray(res.errors) ? res.errors.length : 0;
                const duplicates = Array.isArray(res.duplicates) ? res.duplicates.length : 0;
                store.manualImportReady = errors === 0;
                el.querySelector("#de-import-status").textContent = `${store.t("previewStatus", { valid: String(res.valid ?? 0), errors: String(errors), dups: String(duplicates) })}${errors ? ` · ${store.t("dryRunRequired")}` : ""}`;
                confirm.disabled = !store.manualImportReady;
            })
                .catch(error => { if (!viewScope.isDisposed) {
                importRequest = asyncError(recovery(error));
                el.querySelector("#de-import-status").textContent = recovery(error);
            } })
                .finally(() => { if (importRequest.kind === "loading")
                importRequest = asyncReady(undefined); if (!viewScope.isDisposed)
                dry.disabled = !store.manualImportRows.length; });
        });
        el.querySelector("#de-import")?.addEventListener("click", () => {
            if (importing() || !store.manualImportRows.length || !store.manualImportReady) {
                el.querySelector("#de-import-status").textContent = store.t("dryRunRequired");
                return;
            }
            importRequest = asyncLoading("import");
            const confirm = el.querySelector("#de-import");
            confirm.disabled = true;
            port.manualImport(store.manualImportRows, false, viewScope.signal)
                .then(res => {
                if (viewScope.isDisposed)
                    return;
                notice = `${store.t("importedRows")} ${String(res.imported ?? 0)}`;
                store.manualImportRows = [];
                store.manualImportReady = false;
                return refresh();
            })
                .catch(error => { if (!viewScope.isDisposed) {
                importRequest = asyncError(recovery(error));
                notice = recovery(error);
                void refresh();
            } })
                .finally(() => { if (importRequest.kind === "loading")
                importRequest = asyncReady(undefined); });
        });
    };
    await refresh();
}
export function parseCsv(text) {
    const rows = [];
    let row = [];
    let field = "";
    let quoted = false;
    let closedQuote = false;
    const pushField = () => {
        row.push(field.trim());
        field = "";
        closedQuote = false;
    };
    const pushRow = () => {
        pushField();
        if (row.some(value => value))
            rows.push(row);
        row = [];
    };
    for (let index = 0; index < text.length; index += 1) {
        const char = text[index];
        const next = text[index + 1];
        if (quoted) {
            if (char === '"' && next === '"') {
                field += '"';
                index += 1;
            }
            else if (char === '"') {
                quoted = false;
                closedQuote = true;
            }
            else
                field += char;
            continue;
        }
        if (closedQuote) {
            if (char === ",")
                pushField();
            else if (char === "\n" || char === "\r") {
                if (char === "\r" && next === "\n")
                    index += 1;
                pushRow();
            }
            else if (!/\s/.test(char))
                throw new Error("csv_parse_error");
            continue;
        }
        if (char === '"' && field.length === 0)
            quoted = true;
        else if (char === '"')
            throw new Error("csv_parse_error");
        else if (char === ",")
            pushField();
        else if (char === "\n" || char === "\r") {
            if (char === "\r" && next === "\n")
                index += 1;
            pushRow();
        }
        else
            field += char;
    }
    if (quoted)
        throw new Error("csv_parse_error");
    if (field || row.length || closedQuote)
        pushRow();
    const header = rows[0]?.map(value => value.toLocaleLowerCase().replace(/^\uFEFF/, ""));
    const hasHeader = Boolean(header?.includes("name_kanji"));
    const data = hasHeader ? rows.slice(1) : rows;
    const index = (field, fallback) => {
        if (!hasHeader)
            return fallback;
        return header?.indexOf(field) ?? -1;
    };
    return data.map(columns => ({
        name_kanji: columns[index("name_kanji", 0)] ?? "",
        name_kana: columns[index("name_kana", 1)] ?? "",
        phone: columns[index("phone", 2)] ?? "",
        email: columns[index("email", 3)] ?? "",
    })).filter(record => record.name_kanji);
}
