import type {
  AdminState,
  GenerationCatalog,
  GenerationCatalogItem,
  GenerationControls,
} from "./port.js";
import type { Store } from "./store.js";
import { esc } from "./schema.js";
import { renderPreview, type PreviewViewState } from "./admin-preview.js";
import { renderDatasetExplorer, type DatasetExplorerState } from "./admin-dataset.js";

const NOISE_FIELDS = ["name_kanji", "name_kana", "name_romaji", "date_birth", "postal_code", "address", "phone", "email"];

export interface AdminRenderModel {
  state: AdminState;
  catalog: GenerationCatalog;
  controls: GenerationControls;
  preview: PreviewViewState;
  busy: boolean;
  note: string;
  jobStatus: Record<string, string>;
  dataset: DatasetExplorerState;
}

type LocalizedItem = GenerationCatalogItem & { example_ja?: string; example_en?: string };

function localized(store: Store, item: LocalizedItem, kind: "label" | "description" | "example"): string {
  if (kind === "label") return store.locale === "ja" ? item.label_ja : item.label_en;
  if (kind === "description") return store.locale === "ja" ? item.description_ja : item.description_en;
  return (store.locale === "ja" ? item.example_ja : item.example_en) ?? item.id;
}

function select(name: string, options: string[], value: string): string {
  return `<select id="${name}">${options.map(option => `<option value="${esc(option)}" ${option === value ? "selected" : ""}>${esc(option)}</option>`).join("")}</select>`;
}

function optionList(store: Store, items: GenerationCatalogItem[], value: string): string {
  return items.map(item => `<option value="${esc(item.id)}" ${item.id === value ? "selected" : ""}>${esc(localized(store, item, "label"))}</option>`).join("");
}

function range(attrs: string, value: number, max: number, step = 1): string {
  return `<input type="range" min="0" max="${max}" step="${step}" value="${value}" ${attrs}>`;
}

function renderGenerationControls(model: AdminRenderModel, store: Store): string {
  const { catalog, controls: c } = model;
  const t = (key: string) => esc(store.t(key));
  const bundleLabel = (ids: string[]): string => ids.map(id => {
    const event = catalog.events.find(item => item.id === id);
    return event ? localized(store, event, "label") : id;
  }).join(", ") || store.t("none");
  const eventRows = catalog.events.map(event => `<tr><th scope="row"><strong>${esc(localized(store, event, "label"))}</strong><br><span class="members">${esc(localized(store, event, "description"))}</span></th><td>${esc(event.fields.map(field => store.t(field)).join(" / "))}</td><td>${esc(localized(store, event, "example"))}</td><td>${range(`data-event-rate="${esc(event.id)}" aria-label="${esc(localized(store, event, "label"))}"`, Math.round((c.event_rates[event.id] ?? event.default_rate) * 100), 100)} <output data-event-value="${esc(event.id)}"></output></td></tr>`).join("");
  const qualityShare: Record<string, number> = { average: 1 - (c.poor ?? 0), poor: (c.poor ?? 0) - (c.catastrophic ?? 0), catastrophic: c.catastrophic ?? 0 };
  const qualityRows = ["average", "poor", "catastrophic"].map(key => `<tr><th scope="row">${t(key === "average" ? "qualityAverage" : key === "poor" ? "qualityPoor" : "qualityCatastrophic")}</th><td>${Math.round((qualityShare[key] ?? 0) * 100)}%</td><td>${range(`data-band-scale="${key}" aria-label="${t("corruptionIntensity")}"`, c.band_scale[key] ?? 0, 3, .05)} <output data-band-value="${key}"></output></td><td>${range(`data-missing-rate="${key}" aria-label="${t("residualOmission")}"`, Math.round((c.missing_rates[key] ?? 0) * 100), 100)} <output data-missing-value="${key}"></output></td></tr>`).join("");
  const availabilityRows = catalog.channels.map(channel => `<tr><th scope="row">${esc(channel.id)}<br><span class="members">${t("bundle")}: ${esc(bundleLabel(channel.bundle))}</span></th>${Object.keys(c.field_availability[channel.id] ?? {}).map(field => `<td><label>${esc(store.t(field))} ${range(`data-availability data-channel="${esc(channel.id)}" data-field="${esc(field)}"`, Math.round((c.field_availability[channel.id]?.[field] ?? 1) * 100), 100)} <output data-availability-value data-channel="${esc(channel.id)}" data-field="${esc(field)}"></output></label></td>`).join("")}</tr>`).join("");
  const queryRows = catalog.query_scenarios.map(item => `<tr><th scope="row">${esc(localized(store, item, "label"))}<br><span class="members">${esc(localized(store, item, "description"))}</span></th><td>${range(`data-query-mix="${esc(item.id)}" aria-label="${esc(localized(store, item, "label"))}"`, Math.round((c.query_mix[item.id] ?? item.default_share) * 100), 100)} <output data-query-value="${esc(item.id)}"></output></td></tr>`).join("");
  return `<details id="generation-setup" open><summary>${t("generationProfile")}</summary><p>${t("generationProfileHelp")}</p><div class="toolbar"><button id="gen-reset" class="more" type="button">${t("resetGenerationDefaults")}</button><span class="members">${t("resetGenerationHelp")}</span></div><div class="field-grid">
        <label>${t("profile")} <select id="gen-profile">${optionList(store, catalog.profiles, c.profile)}</select><span id="gen-profile-info" class="members"></span></label>
        <label>${t("difficulty")} <select id="gen-diff">${optionList(store, catalog.difficulties, c.difficulty)}</select><span class="members">${esc(localized(store, catalog.difficulties.find(d => d.id === c.difficulty) ?? catalog.difficulties[0]!, "description"))}</span></label>
        <label>${t("seed")} <input id="gen-seed" type="number" min="0" value="${c.seed}"></label>
        <label>${t("nParties")} <input id="gen-n" type="number" min="0" max="2000000" value="${c.n_parties}"></label>
        <label>${t("meanRecords")} <input id="gen-mean" type="number" min="1" max="20" step=".1" value="${c.mean_records ?? ""}"></label>
        <label>${t("nAgencies")} <input id="gen-agencies" type="number" min="1" max="100" value="${c.n_agencies}"></label>
        <label>${t("personShare")} ${range("id=gen-person-share", Math.round(c.person_share * 100), 100)}<output data-control-output="person_share"></output></label>
        <label>${t("queryShare")} ${range("id=gen-query-share", Math.round(c.query_share * 100), 100)}<output data-control-output="query_share"></output></label>
        <label>${t("twinRate")} ${range("id=gen-twin-rate", Math.round(c.twin_rate * 100), 100)}<output data-control-output="twin_rate"></output></label>
        <label>${t("snapshotDate")} <input id="gen-snapshot" type="date" value="${esc(c.snapshot_date)}"></label>
        <label>${t("queryDate")} <input id="gen-query-date" type="date" value="${esc(c.query_date)}"></label>
      </div></details>
      <details id="difficulty-controls"><summary>${t("difficultyTuning")}</summary><p>${t("difficultyHelp")}</p><div class="field-grid">
        <label>${t("poorShare")} ${range("id=gen-poor", Math.round((c.poor ?? 0) * 100), 100)}<output data-control-output="poor"></output></label>
        <label>${t("catastrophicShare")} ${range("id=gen-catastrophic", Math.round((c.catastrophic ?? 0) * 100), 100)}<output data-control-output="catastrophic"></output></label>
      </div><p id="gen-quality-summary" class="members"></p></details>
      <details id="sabotage-controls"><summary>${t("sabotageCatalog")} <span class="members">${catalog.events.length} ${t("registeredEvents")}</span></summary><p>${t("sabotageHelp")}</p><div class="table-scroll" tabindex="0" role="region" aria-label="${t("sabotageCaption")}"><table id="sabotage-table"><caption>${t("sabotageCaption")}</caption><thead><tr><th>${t("sabotageType")}</th><th>${t("affectedFields")}</th><th>${t("example")}</th><th>${t("intensity")}</th></tr></thead><tbody>${eventRows}</tbody></table></div></details>
      <details id="systematic-controls"><summary>${t("systematicTuning")}</summary><p>${t("systematicHelp")}</p><div class="table-scroll" tabindex="0" role="region" aria-label="${t("qualityImpactCaption")}"><table id="quality-controls"><caption>${t("qualityImpactCaption")}</caption><thead><tr><th>${t("qualityBand")}</th><th>${t("populationShare")}</th><th>${t("corruptionIntensity")}</th><th>${t("residualOmission")}</th></tr></thead><tbody>${qualityRows}</tbody></table></div><label>${t("staleRate")} ${range("id=gen-stale-rate", Math.round((c.stale_rate ?? 0) * 100), 100)}<output data-control-output="stale_rate"></output></label><div class="table-scroll" tabindex="0" role="region" aria-label="${t("availabilityCaption")}"><table><caption>${t("availabilityCaption")}</caption><thead><tr><th>${t("channel")}</th>${NOISE_FIELDS.map(field => `<th>${esc(store.t(field))}</th>`).join("")}</tr></thead><tbody>${availabilityRows}</tbody></table></div></details>
      <details id="query-controls"><summary>${t("queryMixTuning")}</summary><p>${t("queryMixHelp")}</p><div class="table-scroll" tabindex="0" role="region" aria-label="${t("queryScenario")}"><table><thead><tr><th>${t("queryScenario")}</th><th>${t("share")}</th></tr></thead><tbody>${queryRows}</tbody></table></div><p id="query-total" class="members"></p></details>
      <details id="training-controls"><summary>${t("trainingTuning")}</summary><div class="field-grid">
        <label>${t("modelFamily")} ${select("tr-family", model.state.profile.model_families, "off")}</label><label>${t("device")} ${select("tr-device", model.state.profile.devices, "cpu")}</label>
        <label>${t("threads")} <input id="tr-threads" type="number" min="1" max="32" value="${model.state.profile.threads_default}"></label><label>${t("maxRecords")} <input id="tr-max" type="number" min="1000" value="700000"></label>
        <label>${t("trainingSeed")} <input id="tr-seed" type="number" min="0" value="42"></label><label>${t("candidateCap")} <input id="tr-cap" type="number" min="0" value="0"></label>
      </div><div class="toolbar"><button id="tr-go" class="more" type="button" ${model.busy ? "disabled" : ""}>${t("runTrain")}</button></div><p id="tr-job" role="status" aria-live="polite" class="members"></p></details>`;
}

function renderRuntime(model: AdminRenderModel, store: Store): string {
  const t = (key: string) => esc(store.t(key));
  const p = model.state.profile;
  const s = model.state.settings;
  const audit = model.state.audit.slice(0, 20).map(a => `<tr><td>${esc(a.ts)}</td><td>${esc(String(a.actor))}</td><td>${esc(String(a.action))}</td><td>${esc(Object.entries(a).filter(([key]) => !["ts", "actor", "action"].includes(key)).map(([key, value]) => `${key}=${JSON.stringify(value)}`).join(" ").slice(0, 80))}</td></tr>`).join("") || `<tr><td colspan="4">${t("none")}</td></tr>`;
  return `<details id="runtime-controls"><summary>${t("runtimeAndDanger")}</summary><section class="card"><h2>${t("servingTitle")}</h2><p>${t("servingHelp")}</p><div class="field-grid"><label>${t("exactMode")} ${select("set-exact", p.exact_modes, s.exact_mode)}</label><label>${t("engineState")} <span class="${s.engine_warm ? "ok" : "warn"}">${s.engine_warm ? t("engineWarm") : t("engineCold")}</span></label></div><div class="toolbar"><button id="set-go" class="more" type="button">${t("applySettings")}</button><button id="reload-go" class="more" type="button">${t("reloadEngine")}</button></div><p class="members">${t("exactModeNote")}</p></section>
    <section class="card"><h2>${t("dangerZone")}</h2><p>${t("dangerHelp")}</p><div class="field-grid"><label>${t("runSelect")} ${select("dz-run", model.state.runs.map(run => run.id), "")}</label><label><input type="checkbox" id="dz-confirm"> ${t("confirmDestructive")}</label></div><div class="toolbar"><button id="dz-promote" class="more" type="button" disabled>${t("promoteRun")}</button><button id="dz-prune" class="more" type="button" disabled>${t("pruneAborted")}</button><button id="dz-reset" class="more" type="button" disabled>${t("resetDecisions")}</button></div><h3>${t("auditLog")}</h3><div class="table-scroll" tabindex="0" role="region" aria-label="${t("auditLog")}"><table><thead><tr><th>ts</th><th>actor</th><th>action</th><th>detail</th></tr></thead><tbody>${audit}</tbody></table></div></section></details>`;
}

export function renderAdminPage(model: AdminRenderModel, store: Store): string {
  const t = (key: string) => esc(store.t(key));
  return `<div class="panel"><nav class="crumbs" aria-label="${t("crumbs")}">${t("admin")}</nav>
    <div class="admin-workbench" data-panel-layout="admin-workbench">
      <section class="card admin-controls"><h2>${t("dataTrain")}</h2><p>${t("adminHelp")}</p>${renderGenerationControls(model, store)}</section>
      <div id="gen-preview-resizer" class="admin-resizer" data-panel-resizer="preview" role="separator" aria-label="${t("resizePreview")}" aria-orientation="vertical" aria-valuemin="352" aria-valuemax="640" aria-valuenow="420" aria-valuetext="420px" tabindex="0"></div>
      <aside class="admin-preview" tabindex="0" aria-labelledby="gen-live-preview-title"><section class="card" id="gen-live-preview"><header class="panel-heading"><h3 id="gen-live-preview-title">${t("livePreview")}</h3><button id="gen-preview-toggle" class="panel-toggle" type="button" aria-expanded="true" aria-controls="gen-preview-body" data-collapse-label="${t("collapsePanel")}" data-expand-label="${t("expandPanel")}">${t("collapsePanel")}</button></header><div class="toolbar"><button id="gen-go" class="more" type="button" disabled>${t("runGenerate")}</button><button id="gen-preview-go" class="more" type="button">${t("refreshPreview")}</button></div><p id="gen-job" role="status" aria-live="polite" class="members"></p><div id="gen-preview-body"><p class="members">${t("livePreviewHelp")}</p><div id="gen-preview" aria-live="polite" aria-busy="${model.preview.pending}">${renderPreview(model.preview, store)}</div></div></section></aside>
    </div>
    ${renderRuntime(model, store)}
    <div id="dataset-explorer-host">${renderDatasetExplorer(model.dataset, store)}</div>
    ${model.note ? `<p class="notice" role="status">${esc(model.note)}</p>` : ""}</div>`;
}
