import type {
  GenerationCatalogItem,
  GenerationDatasetImpact,
  GenerationPreview,
  GenerationSample,
} from "./port.js";
import type { Store } from "./store.js";
import { esc } from "./schema.js";

export interface PreviewViewState {
  preview: GenerationPreview | null;
  pending: boolean;
  error: string;
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
const number = (value: number): string => value.toLocaleString();
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
  return `<section class="evidence" id="gen-sample"><div class="toolbar"><strong>${esc(store.t("illustrativeExample"))}</strong><label class="members">${esc(store.t("sampleQuality"))} <select id="gen-sample-quality">${qualitySelect}</select></label><label class="members">${esc(store.t("channel"))} <select id="gen-sample-channel">${channelSelect}</select></label></div><p class="members">${esc(store.t("illustrativeExampleHelp"))} · ${esc(store.t("channel"))}: ${esc(sample.channel)} · ${esc(store.t("sampleChangedFields", { n: sample.changes.length }))}</p><div class="table-scroll" tabindex="0" role="region" aria-label="${esc(store.t("sampleCaption"))}"><table><caption>${esc(store.t("sampleCaption"))}</caption><thead><tr><th>${esc(store.t("field"))}</th><th>${esc(store.t("sampleTruth"))}</th><th>${esc(store.t("sampleObserved"))}</th><th>${esc(store.t("sampleEffect"))}</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
}

function renderDatasetImpact(impact: GenerationDatasetImpact, store: Store): string {
  const t = (key: string, values: Record<string, string | number> = {}) => esc(store.t(key, values));
  const qualityRows = impact.quality.map(row => `<tr><th scope="row">${esc(qualityLabel(store, row.id))}</th><td>${percent(row.share)}</td><td>${number(row.records)}</td><td>${row.corruption_scale.toFixed(2)}×</td><td>${percent(row.residual_missing_rate)}</td><td>${number(row.estimated_missing_records)}</td></tr>`).join("");
  const channelRows = impact.channels.map(row => `<tr><th scope="row">${esc(row.id)}</th><td>${percent(row.share)}</td><td>${number(row.records)}</td><td>${esc(row.bundle.join(", ") || store.t("none"))}</td></tr>`).join("");
  const fieldRows = impact.fields.map(row => `<tr><th scope="row">${esc(store.t(row.id))}</th><td>${percent(row.estimated_missing_rate)}</td><td>${number(row.estimated_missing_records)}</td></tr>`).join("");
  const fields = Object.keys(impact.channels[0]?.availability ?? {}).filter(field => FIELD_KEYS.includes(field));
  const availabilityRows = impact.channels.map(channel => `<tr><th scope="row">${esc(channel.id)}</th>${fields.map(field => `<td>${percent(channel.availability[field] ?? 1)}</td>`).join("")}</tr>`).join("");
  return `<section class="evidence" id="dataset-impact"><details><summary>${t("datasetImpact")}</summary><p class="members">${t("datasetImpactHelp")}</p><div class="table-scroll" tabindex="0" role="region" aria-label="${t("qualityImpactCaption")}"><table id="quality-impact"><caption>${t("qualityImpactCaption")}</caption><thead><tr><th>${t("qualityBand")}</th><th>${t("populationShare")}</th><th>${t("expectedRecords")}</th><th>${t("corruptionIntensity")}</th><th>${t("residualOmission")}</th><th>${t("estimatedMissingRecords")}</th></tr></thead><tbody>${qualityRows}</tbody></table></div><div class="table-scroll" tabindex="0" role="region" aria-label="${t("channelImpactCaption")}"><table id="channel-impact"><caption>${t("channelImpactCaption")}</caption><thead><tr><th>${t("channel")}</th><th>${t("populationShare")}</th><th>${t("expectedRecords")}</th><th>${t("bundle")}</th></tr></thead><tbody>${channelRows}</tbody></table></div><div class="table-scroll" tabindex="0" role="region" aria-label="${t("fieldMissingnessCaption")}"><table id="field-impact"><caption>${t("fieldMissingnessCaption")}</caption><thead><tr><th>${t("field")}</th><th>${t("estimatedMissingness")}</th><th>${t("estimatedMissingRecords")}</th></tr></thead><tbody>${fieldRows}</tbody></table></div><details><summary>${t("availabilityCaption")}</summary><div class="table-scroll" tabindex="0" role="region" aria-label="${t("availabilityCaption")}"><table><thead><tr><th>${t("channel")}</th>${fields.map(field => `<th>${esc(store.t(field))}</th>`).join("")}</tr></thead><tbody>${availabilityRows}</tbody></table></div></details><p class="members">${t("weightedCorruptionIntensity")} ${impact.overall.weighted_corruption_scale.toFixed(2)}× · ${t("weightedResidualOmission")} ${percent(impact.overall.weighted_residual_missing_rate)}</p></details></section>`;
}

type PreviewTranslation = (key: string, values?: Record<string, string | number>) => string;

function renderEventTable(preview: GenerationPreview, store: Store, t: PreviewTranslation): string {
  const rows = preview.events.map(event => `<tr><th scope="row">${esc(localized(store, event, "label"))}</th><td>${percent(event.rate)}</td><td>${percent(event.effective_rate)}</td><td>${number(event.estimated_records)}</td></tr>`).join("");
  return `<details><summary>${t("previewEvents")}</summary><div class="table-scroll" tabindex="0" role="region" aria-label="${t("previewEventCaption")}"><table id="preview-events"><caption>${t("previewEventCaption")}</caption><thead><tr><th>${t("sabotageType")}</th><th>${t("eventRate")}</th><th>${t("estimatedEffectiveRate")}</th><th>${t("estimatedRecords")}</th></tr></thead><tbody>${rows}</tbody></table></div></details>`;
}

function renderQueryTable(preview: GenerationPreview, store: Store, t: PreviewTranslation): string {
  const rows = preview.query_mix.map(item => `<tr><th scope="row">${esc(localized(store, item, "label"))}</th><td>${percent(item.share)}</td><td>${number(item.estimated_queries)}</td></tr>`).join("");
  return `<details><summary>${t("previewQueries")}</summary><div class="table-scroll" tabindex="0" role="region" aria-label="${t("queryScenario")}"><table><thead><tr><th>${t("queryScenario")}</th><th>${t("share")}</th><th>${t("estimatedQueries")}</th></tr></thead><tbody>${rows}</tbody></table></div></details>`;
}

function renderPreviewContent(state: PreviewViewState, store: Store): string {
  const t = (key: string, values: Record<string, string | number> = {}) => esc(store.t(key, values));
  if (!state.preview) {
    const error = state.error ? `<p role="alert" class="notice">${esc(state.error)}</p>` : "";
    return `${error}<p class="members">${t(state.pending ? "previewWorking" : "previewRequired")}</p>`;
  }
  const preview = state.preview;
  const errorHtml = state.error ? `<p role="alert" class="notice">${esc(state.error)}</p>` : "";
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
    `<div class="kpi"><div class="card"><span class="l">${t("expectedParties")}</span><div class="v">${number(preview.counts.entities ?? preview.counts.parties)}</div></div><div class="card"><span class="l">${t("expectedPolicies")}</span><div class="v">${number(preview.counts.policies ?? 0)}</div></div><div class="card"><span class="l">${t("expectedRecords")}</span><div class="v">${number(preview.counts.records)}</div></div><div class="card"><span class="l">${t("expectedQueries")}</span><div class="v">${number(preview.counts.queries)}</div></div><div class="card"><span class="l">${t("expectedAgencies")}</span><div class="v">${number(preview.counts.agencies)}</div></div></div>`,
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
  const loading = state.pending;
  const t = (key: string) => esc(store.t(key));
  return `<div class="preview-loading${loading ? " is-loading" : ""}" aria-busy="${state.pending}"><div class="preview-content">${content}</div>${loading ? `<div class="preview-scrim" role="status"><span class="preview-status">${t("previewWorking")}</span></div>` : ""}</div>`;
}
