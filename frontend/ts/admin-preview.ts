import type {
  GenerationCatalogItem,
  GenerationDatasetImpact,
  GenerationPreview,
  GenerationSample,
} from "./port.js";
import type { Store } from "./store.js";
import { esc } from "./schema.js";
import { formatNumber } from "./shared.js";
import type { AsyncState } from "./async-state.js";

export interface PreviewViewState {
  preview: GenerationPreview | null;
  request: AsyncState<GenerationPreview>;
  controlsHash: string;
  sampleQuality: string;
  sampleChannel: string;
}

type LocalizedItem = GenerationCatalogItem & { example_ja?: string; example_en?: string };
const QUALITY_KEYS = ["average", "poor", "catastrophic"] as const;
const FIELD_KEYS = ["name_kanji", "name_kana", "name_romaji", "date_birth", "postal_code", "address", "phone", "email"];

const localized = (store: Store, item: LocalizedItem, kind: "label" | "example"): string => {
  if (kind === "label") return store.locale === "ja" ? item.label_ja : item.label_en;
  return (store.locale === "ja" ? item.example_ja : item.example_en) ?? item.id;
};

const percent = (value: number): string => `${Math.round(value * 100)}%`;
const number = (value: number, store: Store): string => formatNumber(value, store.locale);
const qualityLabel = (store: Store, quality: string): string => store.t(quality === "average" ? "qualityAverage" : quality === "poor" ? "qualityPoor" : "qualityCatastrophic");

function renderSample(
  sample: GenerationSample,
  store: Store,
  qualityOptions: string[],
  channelOptions: string[],
): string {
  const reasons = new Map(sample.changes.map(change => [change.field, change.transformations.join(", ")]));
  const changed = new Set(sample.changes.map(change => change.field));
  const value = (text: string): string => text ? esc(text) : `<span class="muted">${esc(store.t("blankValue"))}</span>`;
  const rows = Object.keys(sample.canonical).map(field => {
    const isChanged = changed.has(field);
    const reason = reasons.get(field) ?? "";
    return `<tr><th scope="row">${esc(store.t(field))}</th><td>${value(sample.canonical[field] ?? "")}</td><td>${isChanged ? `<strong class="warn">${value(sample.observed[field] ?? "")}</strong>` : value(sample.observed[field] ?? "")}</td><td>${isChanged ? esc(reason || store.t("sampleChanged")) : `<span class="muted">${esc(store.t("sampleUnchanged"))}</span>`}</td></tr>`;
  }).join("");
  const qualitySelect = qualityOptions.map(quality => `<option value="${quality}" ${quality === sample.quality ? "selected" : ""}>${esc(qualityLabel(store, quality))}</option>`).join("");
  const channelSelect = channelOptions.map(channel => `<option value="${esc(channel)}" ${channel === sample.channel ? "selected" : ""}>${esc(channel)}</option>`).join("");
  return `<section class="evidence" id="gen-sample"><div class="toolbar"><strong>${esc(store.t("illustrativeExample"))}</strong><label class="members">${esc(store.t("sampleQuality"))} <select id="gen-sample-quality">${qualitySelect}</select></label><label class="members">${esc(store.t("channel"))} <select id="gen-sample-channel">${channelSelect}</select></label></div><p class="members">${esc(store.t("illustrativeExampleHelp"))} · ${esc(store.t("channel"))}: ${esc(sample.channel)} · ${esc(store.t("sampleChangedFields", { n: sample.changes.length }))}</p><div class="table-scroll" tabindex="0" role="region" aria-label="${esc(store.t("sampleCaption"))}"><table><caption>${esc(store.t("sampleCaption"))}</caption><thead><tr><th scope="col">${esc(store.t("field"))}</th><th scope="col">${esc(store.t("sampleTruth"))}</th><th scope="col">${esc(store.t("sampleObserved"))}</th><th scope="col">${esc(store.t("sampleEffect"))}</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
}

function renderDatasetImpact(impact: GenerationDatasetImpact, store: Store): string {
  const t = (key: string, values: Record<string, string | number> = {}) => esc(store.t(key, values));
  const qualityRows = impact.quality.map(row => `<tr><th scope="row">${esc(qualityLabel(store, row.id))}</th><td>${percent(row.share)}</td><td>${number(row.records, store)}</td><td>${row.corruption_scale.toFixed(2)}×</td><td>${percent(row.residual_missing_rate)}</td><td>${number(row.estimated_missing_records, store)}</td></tr>`).join("");
  const channelRows = impact.channels.map(row => `<tr><th scope="row">${esc(row.id)}</th><td>${percent(row.share)}</td><td>${number(row.records, store)}</td><td>${esc(row.bundle.join(", ") || store.t("none"))}</td></tr>`).join("");
  const fieldRows = impact.fields.map(row => `<tr><th scope="row">${esc(store.t(row.id))}</th><td>${percent(row.estimated_missing_rate)}</td><td>${number(row.estimated_missing_records, store)}</td></tr>`).join("");
  const fields = Object.keys(impact.channels[0]?.availability ?? {}).filter(field => FIELD_KEYS.includes(field));
  const availabilityRows = impact.channels.map(channel => `<tr><th scope="row">${esc(channel.id)}</th>${fields.map(field => `<td>${percent(channel.availability[field] ?? 1)}</td>`).join("")}</tr>`).join("");
  return `<section class="evidence" id="dataset-impact"><details><summary>${t("datasetImpact")}</summary><p class="members">${t("datasetImpactHelp")}</p><div class="table-scroll" tabindex="0" role="region" aria-label="${t("qualityImpactCaption")}"><table id="quality-impact"><caption>${t("qualityImpactCaption")}</caption><thead><tr><th scope="col">${t("qualityBand")}</th><th scope="col">${t("populationShare")}</th><th scope="col">${t("expectedRecords")}</th><th scope="col">${t("corruptionIntensity")}</th><th scope="col">${t("residualOmission")}</th><th scope="col">${t("estimatedMissingRecords")}</th></tr></thead><tbody>${qualityRows}</tbody></table></div><div class="table-scroll" tabindex="0" role="region" aria-label="${t("channelImpactCaption")}"><table id="channel-impact"><caption>${t("channelImpactCaption")}</caption><thead><tr><th scope="col">${t("channel")}</th><th scope="col">${t("populationShare")}</th><th scope="col">${t("expectedRecords")}</th><th scope="col">${t("bundle")}</th></tr></thead><tbody>${channelRows}</tbody></table></div><div class="table-scroll" tabindex="0" role="region" aria-label="${t("fieldMissingnessCaption")}"><table id="field-impact"><caption>${t("fieldMissingnessCaption")}</caption><thead><tr><th scope="col">${t("field")}</th><th scope="col">${t("estimatedMissingness")}</th><th scope="col">${t("estimatedMissingRecords")}</th></tr></thead><tbody>${fieldRows}</tbody></table></div><details><summary>${t("availabilityCaption")}</summary><div class="table-scroll" tabindex="0" role="region" aria-label="${t("availabilityCaption")}"><table><caption class="members">${t("availabilityCaption")}</caption><thead><tr><th scope="col">${t("channel")}</th>${fields.map(field => `<th scope="col">${esc(store.t(field))}</th>`).join("")}</tr></thead><tbody>${availabilityRows}</tbody></table></div></details><p class="members">${t("weightedCorruptionIntensity")} ${impact.overall.weighted_corruption_scale.toFixed(2)}× · ${t("weightedResidualOmission")} ${percent(impact.overall.weighted_residual_missing_rate)}</p></details></section>`;
}

type PreviewTranslation = (key: string, values?: Record<string, string | number>) => string;

function renderEventTable(preview: GenerationPreview, store: Store, t: PreviewTranslation): string {
  const rows = preview.events.map(event => `<tr><th scope="row">${esc(localized(store, event, "label"))}</th><td>${percent(event.rate)}</td><td>${percent(event.effective_rate)}</td><td>${number(event.estimated_records, store)}</td></tr>`).join("");
  return `<details><summary>${t("previewEvents")}</summary><div class="table-scroll" tabindex="0" role="region" aria-label="${t("previewEventCaption")}"><table id="preview-events"><caption>${t("previewEventCaption")}</caption><thead><tr><th scope="col">${t("sabotageType")}</th><th scope="col">${t("eventRate")}</th><th scope="col">${t("estimatedEffectiveRate")}</th><th scope="col">${t("estimatedRecords")}</th></tr></thead><tbody>${rows}</tbody></table></div></details>`;
}

function renderQueryTable(preview: GenerationPreview, store: Store, t: PreviewTranslation): string {
  const rows = preview.query_mix.map(item => `<tr><th scope="row">${esc(localized(store, item, "label"))}</th><td>${percent(item.share)}</td><td>${number(item.estimated_queries, store)}</td></tr>`).join("");
  return `<details><summary>${t("previewQueries")}</summary><div class="table-scroll" tabindex="0" role="region" aria-label="${t("queryScenario")}"><table><caption class="members">${t("queryScenario")}</caption><thead><tr><th scope="col">${t("queryScenario")}</th><th scope="col">${t("share")}</th><th scope="col">${t("estimatedQueries")}</th></tr></thead><tbody>${rows}</tbody></table></div></details>`;
}

function renderPreviewContent(state: PreviewViewState, store: Store): string {
  const t = (key: string, values: Record<string, string | number> = {}) => esc(store.t(key, values));
  if (!state.preview) {
    const error = state.request.kind === "error" ? `<p role="alert" class="notice">${esc(state.request.message)}</p>` : "";
    return `${error}<p class="members">${t(state.request.kind === "loading" ? "previewWorking" : "previewRequired")}</p>`;
  }
  const preview = state.preview;
  const errorHtml = state.request.kind === "error" ? `<p role="alert" class="notice">${esc(state.request.message)}</p>` : "";
  const examples = preview.examples?.length ? preview.examples : Object.values(preview.samples);
  const quality = state.sampleQuality || "average";
  const qualityExamples = examples.filter(example => example.quality === quality);
  const fallback = preview.samples[quality] ?? preview.samples.average;
  const sample = qualityExamples.find(example => example.channel === state.sampleChannel) ?? qualityExamples[0] ?? fallback;
  if (!sample) return `${errorHtml}<p class="notice" role="alert">${t("previewSchemaMismatch")}</p>`;
  const qualityOptions = [...new Set([...QUALITY_KEYS, ...examples.map(example => example.quality)])];
  const channelOptions = [...new Set(qualityExamples.map(example => example.channel).concat(sample.channel))];
  const warningHtml = preview.warnings.length
    ? `<div class="notice" role="status"><strong>${t("previewWarnings")}</strong><ul>${preview.warnings.map(w => `<li>${esc(store.locale === "ja" ? w.ja : w.en)}</li>`).join("")}</ul></div>`
    : `<p class="ok">${t("previewLooksGood")}</p>`;
  return [
    `<section class="notice" id="gen-time-window"><strong>${t("temporalWindow")}</strong> ${t("referenceSnapshot")} <b>${esc(preview.time_window.snapshot_date)}</b> → ${t("incomingQueryDate")} <b>${esc(preview.time_window.query_date)}</b> · ${t("temporalGap", { n: preview.time_window.gap_days })}</section>`,
    `<div class="kpi"><div class="card"><span class="l">${t("expectedParties")}</span><div class="v">${number(preview.counts.entities ?? preview.counts.parties, store)}</div></div><div class="card"><span class="l">${t("expectedPolicies")}</span><div class="v">${number(preview.counts.policies ?? 0, store)}</div></div><div class="card"><span class="l">${t("expectedRecords")}</span><div class="v">${number(preview.counts.records, store)}</div></div><div class="card"><span class="l">${t("expectedQueries")}</span><div class="v">${number(preview.counts.queries, store)}</div></div><div class="card"><span class="l">${t("expectedAgencies")}</span><div class="v">${number(preview.counts.agencies, store)}</div></div></div>`,

    warningHtml,
    `<p class="members">${esc(store.locale === "ja" ? preview.estimate_note.ja : preview.estimate_note.en)} · ${esc(store.t("previewHash", { hash: preview.config_hash }))}</p>`,
    renderDatasetImpact(preview.dataset_impact, store),
    renderSample(sample, store, qualityOptions, channelOptions),
    renderEventTable(preview, store, t),
    renderQueryTable(preview, store, t),
    `<p class="members">${t("previewMissingness")} ${percent(preview.missingness.estimated_average_rate)} · ${t("weightedBandScale")} ${preview.weighted_band_scale.toFixed(2)}×</p>`,
  ].join("");
}

export function renderPreview(state: PreviewViewState, store: Store): string {
  const content = renderPreviewContent(state, store);
  const loading = state.request.kind === "loading";
  const t = (key: string) => esc(store.t(key));
  return `<div class="preview-loading${loading ? " is-loading" : ""}" aria-busy="${loading}"><div class="preview-content">${content}</div>${loading ? `<div class="preview-scrim" role="status"><span class="preview-status">${t("previewWorking")}</span></div>` : ""}</div>`;
}
