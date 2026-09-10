import { esc } from "./schema.js";
import { pollJob } from "./core.js";
import { bindGenerationControls, cloneGenerationControls, resetGenerationControls, syncControlOutputs } from "./admin-controls.js";
import { renderAdminPage } from "./admin-render.js";
import { renderPreview } from "./admin-preview.js";
import { bindPanelLayout } from "./panel-layout.js";
import { bindDatasetExplorer, createDatasetExplorerState, refreshDatasetGenerations } from "./admin-dataset.js";
function unavailableCard(store) {
    const wrapper = document.createElement("div");
    wrapper.className = "panel";
    wrapper.innerHTML = `<div class="empty"><h3>${esc(store.t("adminDisabledTitle"))}</h3><p>${esc(store.t("adminDisabledHelp"))}</p></div>`;
    return wrapper;
}
export async function mountAdmin(el, port, store, scope) {
    const lifetime = scope;
    let state;
    let catalog;
    try {
        [state, catalog] = await Promise.all([port.adminState(), port.adminGenerationConfig()]);
        if (lifetime?.isDisposed)
            return;
    }
    catch {
        el.replaceChildren(unavailableCard(store));
        return;
    }
    let generations = [];
    try {
        generations = await port.adminGenerations();
        if (lifetime?.isDisposed)
            return;
    }
    catch {
        generations = [];
    }
    const ui = {
        state,
        catalog,
        controls: cloneGenerationControls(catalog.defaults),
        preview: { preview: null, pending: false, error: "", sampleQuality: "average", sampleChannel: "paper_ocr" },
        note: "",
        busy: false,
        confirm: false,
        jobStatus: {},
        dataset: createDatasetExplorerState(generations),
    };
    let previewTimer;
    let previewRequest = 0;
    let panelLayout;
    let datasetBinding;
    lifetime.add({ dispose: () => datasetBinding?.dispose() });
    const updatePreviewDom = () => {
        if (lifetime?.isDisposed)
            return;
        const output = el.querySelector("#gen-preview");
        if (output) {
            output.setAttribute("aria-busy", String(ui.preview.pending));
            output.innerHTML = renderPreview(ui.preview, store);
        }
        const generate = el.querySelector("#gen-go");
        if (generate)
            generate.disabled = ui.busy || ui.preview.pending || !ui.preview.preview;
        const refresh = el.querySelector("#gen-preview-go");
        if (refresh)
            refresh.disabled = ui.preview.pending;
        el.querySelector("#gen-sample-quality")?.addEventListener("change", event => {
            ui.preview.sampleQuality = event.target.value;
            updatePreviewDom();
        });
        el.querySelector("#gen-sample-channel")?.addEventListener("change", event => {
            ui.preview.sampleChannel = event.target.value;
            updatePreviewDom();
        });
    };
    const previewError = (error) => {
        const message = String(error.message ?? error);
        return message === "preview_schema_mismatch"
            ? store.t("previewSchemaMismatch")
            : `${store.t("previewError")}: ${message}`;
    };
    const requestPreview = (immediate = false) => {
        if (lifetime?.isDisposed)
            return;
        ui.preview.pending = true;
        ui.preview.error = "";
        updatePreviewDom();
        if (previewTimer)
            clearTimeout(previewTimer);
        const run = () => {
            if (lifetime?.isDisposed)
                return;
            const request = ++previewRequest;
            const controls = cloneGenerationControls(ui.controls);
            void port.adminGenerationPreview(controls).then(result => {
                if (lifetime?.isDisposed || request !== previewRequest)
                    return;
                ui.preview.preview = result;
                ui.preview.pending = false;
                updatePreviewDom();
            }).catch(error => {
                if (lifetime?.isDisposed || request !== previewRequest)
                    return;
                ui.preview.pending = false;
                ui.preview.error = previewError(error);
                updatePreviewDom();
            });
        };
        if (immediate)
            run();
        else
            previewTimer = lifetime.addTimeout(run, 220);
    };
    const note = (text) => {
        if (lifetime?.isDisposed)
            return;
        ui.note = text;
        void paint();
    };
    const renderJob = (out, job, selector) => {
        const progress = job.progress;
        if (progress?.pct != null) {
            const eta = progress.eta_seconds ?? 0;
            const mmss = `${Math.floor(eta / 60)}:${String(eta % 60).padStart(2, "0")}`;
            out.innerHTML = `<span class="meter"><i style="width:${Math.min(100, Math.max(0, progress.pct))}%"></i></span> ${esc(store.t("phase_" + (progress.phase ?? "generating")))} ${Math.round(progress.pct)}% · ${esc(store.t("etaShort", { t: mmss }))} · ${progress.done?.toLocaleString() ?? ""}/${progress.total?.toLocaleString() ?? ""}`;
        }
        else {
            out.textContent = `${store.t(job.stage)} · ${store.t("elapsed", { s: (job.elapsed_ms / 1000).toFixed(1) })}`;
        }
        ui.jobStatus[selector] = out.innerHTML;
    };
    const runJob = (action, params, outSelector) => {
        if (lifetime?.isDisposed || ui.busy)
            return;
        ui.busy = true;
        updatePreviewDom();
        const out = el.querySelector(outSelector);
        if (out)
            out.textContent = store.t("jobStarted");
        delete ui.jobStatus[outSelector];
        port.adminStart(action, params).then(job => pollJob(() => port.adminJob(job.id), job, 600, current => {
            if (lifetime?.isDisposed)
                return;
            if (out)
                renderJob(out, current, outSelector);
        }, lifetime.signal).then(async (done) => {
            if (lifetime?.isDisposed)
                return;
            ui.busy = false;
            if (done.stage === "complete") {
                const result = done.result ?? {};
                ui.note = `${store.t("jobDone")} ${String(result.run ?? result.generation ?? "")} (${String(result.fingerprint ?? "")})`;
                ui.jobStatus[outSelector] = `${store.t("jobDone")} · ${(done.elapsed_ms / 1000).toFixed(1)}s`;
            }
            else {
                const error = store.t(done.error ?? "matching_failed");
                ui.note = `${error} ${store.t("errorNextStep")}`;
                ui.jobStatus[outSelector] = `${error} ${store.t("errorNextStep")}`;
            }
            ui.state = await port.adminState();
            try {
                await refreshDatasetGenerations(ui.dataset, port);
            }
            catch {
                ui.dataset.error = store.t("datasetUnavailable");
            }
            await paint();
        })).catch(error => {
            if (lifetime?.isDisposed)
                return;
            ui.busy = false;
            note(`${store.t(String(error.message ?? error))} ${store.t("errorNextStep")}`);
        });
    };
    const apply = (action, params, okKey) => {
        port.adminAction(action, params)
            .then(() => note(store.t(okKey)))
            .then(() => port.adminState().then(next => { ui.state = next; void paint(); }))
            .catch(error => note(`${store.t(String(error.message ?? error))} ${store.t("errorNextStep")}`));
    };
    const paint = async () => {
        if (lifetime?.isDisposed)
            return;
        const previousDetails = new Set([...el.querySelectorAll("details[id]")].filter(details => details.open).map(details => details.id));
        const preserveDetails = el.querySelector("details[id]") !== null;
        el.innerHTML = renderAdminPage({ state: ui.state, catalog: ui.catalog, controls: ui.controls, preview: ui.preview, busy: ui.busy, note: ui.note, jobStatus: ui.jobStatus, dataset: ui.dataset }, store);
        if (preserveDetails) {
            el.querySelectorAll("details[id]").forEach(details => { details.open = previousDetails.has(details.id); });
        }
        for (const [selector, html] of Object.entries(ui.jobStatus)) {
            const output = el.querySelector(selector);
            if (output)
                output.innerHTML = html;
        }
        bindGenerationControls(el, ui.catalog, ui.controls, store, () => requestPreview());
        el.querySelector("#gen-reset")?.addEventListener("click", () => resetGenerationControls(el, ui.catalog, ui.controls, ui.catalog.defaults, store, () => requestPreview(true)));
        panelLayout?.dispose();
        panelLayout = lifetime.add(bindPanelLayout(el, store));
        datasetBinding?.dispose();
        datasetBinding = undefined;
        const datasetHost = el.querySelector("#dataset-explorer-host");
        if (datasetHost)
            datasetBinding = bindDatasetExplorer(datasetHost, ui.dataset, port, store);
        syncControlOutputs(el, ui.catalog, ui.controls, store);
        el.querySelector("#gen-go")?.addEventListener("click", () => runJob("generate", cloneGenerationControls(ui.controls), "#gen-job"));
        el.querySelector("#gen-preview-go")?.addEventListener("click", () => requestPreview(true));
        el.querySelector("#tr-go")?.addEventListener("click", () => runJob("train", {
            model_family: el.querySelector("#tr-family").value,
            device: el.querySelector("#tr-device").value,
            threads: Number(el.querySelector("#tr-threads").value) || 2,
            max_records: Number(el.querySelector("#tr-max").value) || 700_000,
            seed: Number(el.querySelector("#tr-seed").value) || 42,
            candidate_cap: Number(el.querySelector("#tr-cap").value) || 0,
        }, "#tr-job"));
        el.querySelector("#set-go")?.addEventListener("click", () => apply("settings", { exact_mode: el.querySelector("#set-exact").value }, "settingsApplied"));
        el.querySelector("#reload-go")?.addEventListener("click", () => apply("reload_engine", {}, "engineReloaded"));
        el.querySelector("#dz-confirm")?.addEventListener("change", event => {
            ui.confirm = event.target.checked;
            el.querySelectorAll("#dz-promote, #dz-prune, #dz-reset").forEach(button => { button.disabled = !ui.confirm; });
        });
        const runValue = () => el.querySelector("#dz-run").value;
        el.querySelector("#dz-promote")?.addEventListener("click", () => apply("promote", { run_id: runValue() }, "promoted"));
        el.querySelector("#dz-prune")?.addEventListener("click", () => apply("prune", {}, "pruned"));
        el.querySelector("#dz-reset")?.addEventListener("click", () => apply("reset_decisions", { run_id: runValue() }, "decisionsReset"));
        updatePreviewDom();
    };
    await paint();
    requestPreview(true);
}
