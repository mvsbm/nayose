import { disposable } from "./lifecycle.js";
import { esc } from "./schema.js";
import { pollJob } from "./core.js";
import { bindGenerationControls, cloneGenerationControls, generationControlsHash, resetGenerationControls, syncControlOutputs } from "./admin-controls.js";
import { renderAdminPage } from "./admin-render.js";
import { renderPreview } from "./admin-preview.js";
import { bindPanelLayout } from "./panel-layout.js";
import { bindDatasetExplorer, createDatasetExplorerState, refreshDatasetGenerations } from "./admin-dataset.js";
import { errorKey, presentError } from "./api-error.js";
import { renderJobStatus } from "./admin-job.js";
import { asyncError, asyncIdle, asyncLoading, asyncReady } from "./async-state.js";
function unavailableCard(store) {
    const wrapper = document.createElement("div");
    wrapper.className = "panel";
    wrapper.innerHTML = `<div class="empty"><h3>${esc(store.t("adminDisabledTitle"))}</h3><p>${esc(store.t("adminDisabledHelp"))}</p><button id="admin-retry" type="button" class="button button-secondary">${esc(store.t("retry"))}</button></div>`;
    return wrapper;
}
export async function mountAdmin(el, port, store, scope) {
    const lifetime = scope;
    el.innerHTML = `<div class="panel" role="status" aria-live="polite" aria-label="${esc(store.t("loading"))}"><div class="skel skel-tall"><div class="card"></div></div></div>`;
    let state;
    let catalog;
    try {
        [state, catalog] = await Promise.all([port.adminState(lifetime.signal), port.adminGenerationConfig(lifetime.signal)]);
        if (lifetime?.isDisposed)
            return;
    }
    catch {
        el.replaceChildren(unavailableCard(store));
        el.querySelector("#admin-retry")?.addEventListener("click", () => window.location.reload());
        return;
    }
    let config = null;
    try {
        config = await port.adminConfig(lifetime.signal);
        if (lifetime?.isDisposed)
            return;
    }
    catch {
        config = null;
    }
    let generations = [];
    try {
        generations = await port.adminGenerations(lifetime.signal);
        if (lifetime?.isDisposed)
            return;
    }
    catch {
        generations = [];
    }
    const ui = {
        state,
        config,
        catalog,
        controls: cloneGenerationControls(catalog.defaults),
        preview: { preview: null, request: { kind: "idle" }, controlsHash: "", sampleQuality: "average", sampleChannel: "paper_ocr" },
        note: "",
        request: asyncIdle(),
        confirm: false,
        jobStatus: {},
        dataset: createDatasetExplorerState(generations),
        section: "data-training",
    };
    const adminBusy = () => ui.request.kind === "loading";
    let previewTimer;
    let previewRequest = 0;
    let previewController;
    let panelLayout;
    let datasetBinding;
    lifetime.add({ dispose: () => datasetBinding?.dispose() });
    const updatePreviewDom = () => {
        if (lifetime?.isDisposed)
            return;
        const output = el.querySelector("#gen-preview");
        if (output) {
            output.setAttribute("aria-busy", String(ui.preview.request.kind === "loading"));
            output.innerHTML = renderPreview(ui.preview, store);
        }
        const generate = el.querySelector("#gen-go");
        const previewPending = ui.preview.request.kind === "loading";
        if (generate)
            generate.disabled = adminBusy() || previewPending || !ui.preview.preview || ui.preview.controlsHash !== generationControlsHash(ui.controls);
        const refresh = el.querySelector("#gen-preview-go");
        if (refresh)
            refresh.disabled = previewPending;
        const quality = el.querySelector("#gen-sample-quality");
        if (quality && quality.dataset.bound !== "true") {
            quality.dataset.bound = "true";
            quality.addEventListener("change", event => { ui.preview.sampleQuality = event.target.value; updatePreviewDom(); });
        }
        const channel = el.querySelector("#gen-sample-channel");
        if (channel && channel.dataset.bound !== "true") {
            channel.dataset.bound = "true";
            channel.addEventListener("change", event => { ui.preview.sampleChannel = event.target.value; updatePreviewDom(); });
        }
    };
    const previewError = (error) => {
        const key = errorKey(error);
        return key === "preview_schema_mismatch"
            ? store.t("previewSchemaMismatch")
            : `${store.t("previewError")}: ${presentError(error, store.t.bind(store))}`;
    };
    const requestPreview = (immediate = false) => {
        if (lifetime?.isDisposed)
            return;
        ui.preview.request = asyncLoading(String(previewRequest + 1));
        ui.preview.controlsHash = "";
        previewController?.abort();
        previewController = new AbortController();
        updatePreviewDom();
        lifetime.clearTimeout(previewTimer);
        const run = () => {
            if (lifetime?.isDisposed)
                return;
            const request = ++previewRequest;
            const controls = cloneGenerationControls(ui.controls);
            const signal = previewController?.signal;
            void port.adminGenerationPreview(controls, signal).then(result => {
                if (lifetime?.isDisposed || request !== previewRequest)
                    return;
                ui.preview.preview = result;
                ui.preview.request = asyncReady(result);
                ui.preview.controlsHash = generationControlsHash(controls);
                updatePreviewDom();
            }).catch(error => {
                if (lifetime?.isDisposed || request !== previewRequest)
                    return;
                if (errorKey(error) === "request_cancelled")
                    return;
                ui.preview.request = asyncError(previewError(error), String(request));
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
        const status = { job };
        ui.jobStatus[selector] = status;
        out.innerHTML = renderJobStatus(status, store);
    };
    const runJob = (action, params, outSelector) => {
        if (lifetime?.isDisposed || adminBusy())
            return;
        ui.request = asyncLoading(`job:${action}`);
        updatePreviewDom();
        const out = el.querySelector(outSelector);
        if (out)
            out.textContent = store.t("jobStarted");
        delete ui.jobStatus[outSelector];
        port.adminStart(action, params, lifetime.signal).then(job => pollJob(() => port.adminJob(job.id, lifetime.signal), job, 600, current => {
            if (lifetime?.isDisposed)
                return;
            if (out)
                renderJob(out, current, outSelector);
        }, lifetime.signal).then(async (done) => {
            if (lifetime?.isDisposed)
                return;
            ui.request = asyncReady(undefined);
            if (done.poll_status === "timeout") {
                ui.note = store.t("jobStillRunning");
                ui.jobStatus[outSelector] = { message: store.t("jobStillRunning") };
            }
            else if (done.poll_status === "cancelled") {
                ui.note = store.t("cancelled");
                ui.jobStatus[outSelector] = { message: store.t("cancelled") };
            }
            else if (done.stage === "complete") {
                const result = done.result ?? {};
                ui.note = `${store.t("jobDone")} ${String(result.run ?? result.generation ?? "")} (${String(result.fingerprint ?? "")})`;
                window.dispatchEvent(new Event("nayose:runs-changed"));
                ui.jobStatus[outSelector] = { message: `${store.t("jobDone")} · ${(done.elapsed_ms / 1000).toFixed(1)}s` };
            }
            else {
                const error = store.t(done.error ?? "matching_failed");
                ui.note = `${error} ${store.t("errorNextStep")}`;
                ui.jobStatus[outSelector] = { message: `${error} ${store.t("errorNextStep")}` };
            }
            ui.state = await port.adminState(lifetime.signal);
            try {
                await refreshDatasetGenerations(ui.dataset, port, lifetime.signal);
            }
            catch {
                ui.dataset.request = asyncError(store.t("datasetUnavailable"));
            }
            await paint();
        })).catch(error => {
            if (lifetime?.isDisposed)
                return;
            ui.request = asyncError(`${presentError(error, store.t.bind(store))} ${store.t("errorNextStep")}`);
            note(`${presentError(error, store.t.bind(store))} ${store.t("errorNextStep")}`);
        });
    };
    const apply = (action, params, okKey) => {
        if (lifetime.isDisposed || adminBusy())
            return;
        ui.request = asyncLoading(`action:${action}`);
        void paint();
        port.adminAction(action, params, lifetime.signal)
            .then(() => { ui.note = store.t(okKey); return port.adminState(lifetime.signal); })
            .then(next => { ui.state = next; ui.request = asyncReady(undefined); })
            .catch(error => {
            ui.request = asyncError(`${presentError(error, store.t.bind(store))} ${store.t("errorNextStep")}`);
            ui.note = `${presentError(error, store.t.bind(store))} ${store.t("errorNextStep")}`;
        })
            .finally(() => { void paint(); });
    };
    const paint = async () => {
        if (lifetime?.isDisposed)
            return;
        const previousDetails = new Set([...el.querySelectorAll("details[id]")].filter(details => details.open).map(details => details.id));
        const preserveDetails = el.querySelector("details[id]") !== null;
        el.innerHTML = renderAdminPage({ state: ui.state, config: ui.config, catalog: ui.catalog, controls: ui.controls, preview: ui.preview, request: ui.request, confirm: ui.confirm, note: ui.note, dataset: ui.dataset, section: ui.section }, store);
        if (preserveDetails) {
            el.querySelectorAll("details[id]").forEach(details => { details.open = previousDetails.has(details.id); });
        }
        for (const [selector, status] of Object.entries(ui.jobStatus)) {
            const output = el.querySelector(selector);
            if (output)
                output.innerHTML = renderJobStatus(status, store);
        }
        el.querySelectorAll("[data-admin-section]").forEach(button => button.addEventListener("click", () => {
            ui.section = button.dataset.adminSection;
            void paint();
        }));
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
            el.querySelectorAll("#dz-promote, #dz-prune, #dz-reset").forEach(button => { button.disabled = !ui.confirm || adminBusy(); });
        });
        const runValue = () => el.querySelector("#dz-run").value;
        el.querySelector("#dz-promote")?.addEventListener("click", () => apply("promote", { run_id: runValue() }, "promoted"));
        el.querySelector("#dz-prune")?.addEventListener("click", () => apply("prune", {}, "pruned"));
        el.querySelector("#dz-reset")?.addEventListener("click", () => apply("reset_decisions", { run_id: runValue() }, "decisionsReset"));
        updatePreviewDom();
    };
    lifetime.add(disposable(() => {
        previewController?.abort();
        lifetime.clearTimeout(previewTimer);
    }));
    lifetime.add(disposable(store.on("locale", () => void paint())));
    await paint();
    requestPreview(true);
}
