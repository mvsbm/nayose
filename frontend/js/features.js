import { mountMatchingSearch } from "./match-view.js";
import { mountEntityExplorer } from "./entity-explorer.js";
import { mountEntityDetail } from "./entity-view.js";
import { asNumber, checkedKey, clamp01, diffFields } from "./core.js";
import { esc, str } from "./schema.js";
import { icon } from "./icons.js";
import { createFlipAnimator, formatNumber, renderBreadcrumb } from "./shared.js";
import { disposable } from "./lifecycle.js";
import { mountView } from "./view-runtime.js";
import { bindPanelSplit } from "./panel-layout.js";
import { presentError } from "./api-error.js";
const COMPARE_SPLIT_DEFAULT = 440;
const COMPARE_SPLIT_MIN = 280;
const COMPARE_SPLIT_MAX = 720;
const SKELETON = (k = 3) => `<div class="skel">${Array.from({ length: k }, () => '<div class="card"></div>').join("")}</div>`;
const crumb = (store, href, label) => renderBreadcrumb(store, [{ href, label }]);
const bar = (v) => `<progress class="meter" max="1" value="${clamp01(v)}" aria-hidden="true"></progress>`;
const heatClass = (value, maximum) => {
    const ratio = maximum > 0 ? value / maximum : 0;
    if (ratio >= 0.75)
        return "heat-max";
    if (ratio >= 0.5)
        return "heat-high";
    if (ratio >= 0.25)
        return "heat-mid";
    return "heat-low";
};
const metricKey = (label) => ({
    "candidate recall": "candidate_recall", "B3 F1": "cluster_f1", "top-1 identity": "top1_identity", MRR: "mrr", review: "review_rate", "no-match": "no_match_rate",
}[label] ?? label);
const metricHelpKey = (label) => `${metricKey(label)}_help`;
const translatedKey = (store, key) => store.t(key);
const formatMetric = (metric, unit, locale) => {
    if (metric.unit === "count")
        return formatNumber(metric.value, locale);
    return unit === "percent" ? `${(metric.value * 100).toFixed(1)}%` : metric.value.toFixed(4);
};
const localizedId = (store, id) => translatedKey(store, id);
export async function mountSearch(el, port, store, scope) {
    await mountMatchingSearch(el, port, store, scope);
}
export async function mountReview(el, port, store, viewScope) {
    const reviewParams = new URLSearchParams(location.hash.split("?")[1] ?? "");
    let offset = Math.max(0, Number(reviewParams.get("offset")) || 0);
    let minP = clamp01((Number(reviewParams.get("min_p")) || 0) / 100);
    const requestedPageSize = Number(reviewParams.get("size"));
    let pageSize = [25, 50, 100, 200, 500].includes(requestedPageSize) ? requestedPageSize : 200;
    let filterEntity = reviewParams.get("nayose") ?? "";
    const syncReviewUrl = () => {
        const params = new URLSearchParams();
        if (minP)
            params.set("min_p", String(Math.round(minP * 100)));
        if (offset)
            params.set("offset", String(offset));
        if (pageSize !== 200)
            params.set("size", String(pageSize));
        if (filterEntity)
            params.set("nayose", filterEntity);
        window.history.replaceState(null, "", `#/review${params.toString() ? `?${params}` : ""}`);
    };
    const key = (l, r) => checkedKey(store.run, l, r);
    const hiddenBelow = (page) => store.t("hiddenBelow", { n: Math.max(0, page.total - page.rows.length - offset), m: page.total });
    const histogramHtml = (page) => {
        const max = Math.max(1, ...page.histogram.map(([, c]) => c));
        const width = 100 / Math.max(1, page.histogram.length);
        const bars = page.histogram
            .map(([lower, count], index) => {
            const height = Math.max(2, Math.round((count / max) * 100));
            const x = index * width;
            const title = `${(lower * 100).toFixed(0)}%–${((lower + 0.05) * 100).toFixed(0)}%: ${count}`;
            return `<rect class="hist-bar" x="${x.toFixed(2)}" y="${100 - height}" width="${Math.max(1, width - 0.5).toFixed(2)}" height="${height}" rx="0.5"><title>${esc(title)}</title></rect>`;
        })
            .join("");
        const mark = (p, title) => `<line class="hist-mark" x1="${(clamp01(p) * 100).toFixed(2)}" x2="${(clamp01(p) * 100).toFixed(2)}" y1="0" y2="100"><title>${esc(title)}</title></line>`;
        const pairMark = page.pair_auto_threshold
            ? mark(page.pair_auto_threshold, store.t("pairAutoMark", { p: (page.pair_auto_threshold * 100).toFixed(1) + "%" }))
            : "";
        const chartLabel = `${store.t("scoreBands")}: ${page.histogram.length}`;
        return `<div class="card"><h3>${esc(store.t("scoreBands"))}</h3>
      <div class="hist"><svg class="hist-chart" viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="${esc(chartLabel)}">${bars}${mark(minP, store.t("scoreFilter"))}${pairMark}</svg></div>
      <p class="text-meta">${hiddenBelow(page)}</p></div>`;
    };
    const refresh = (initial) => mountView(el, {
        skeleton: initial ? SKELETON() : undefined,
        loadingText: store.t("loading"),
        load: (signal) => port.review(minP, offset, pageSize, filterEntity, signal),
        render: (page) => `<div class="panel">${crumb(store, "#/review", store.t("review"))}
      <div class="card"><div class="toolbar">
        <h3>${esc(store.t("reviewTitle"))}</h3>
        <label class="text-meta">${esc(store.t("scoreFilter"))}
          <input class="slider" type="range" id="minp" min="0" max="100" step="1" value="${Math.round(minP * 100)}" aria-valuetext="${Math.round(minP * 100)}%"></label>
        <output class="slider-output text-meta" for="minp" id="minp-val">${Math.round(minP * 100)}%</output>
        <label class="text-meta">${esc(store.t("pageSize"))}<select id="page-size" aria-label="${esc(store.t("pageSize"))}">
          ${[25, 50, 100, 200, 500].map(n => `<option ${n === pageSize ? "selected" : ""}>${n}</option>`).join("")}
        </select></label>
        <span class="text-meta" id="count" role="status" aria-live="polite">${esc(store.t("reviewCount", { n: page.total, read: store.checked.size }))}</span>
        ${filterEntity ? `<button type="button" class="button button-chip warn" id="filter-clear" title="${esc(filterEntity)}">${esc(store.t("filterByEntity", { id: filterEntity }))} ${icon("close")}</button>` : ""}
        <label class="text-meta">${esc(store.t("reviewer"))}<input id="reviewer" class="text-input" type="text" size="10" value="${esc(store.reviewer)}"></label>
        <button type="button" class="button button-secondary" id="undo">${esc(store.t("undo"))}</button>
        <button type="button" class="button button-secondary" id="review-prev" ${offset === 0 ? "disabled" : ""}>${esc(store.t("previous"))}</button>
        <button type="button" class="button button-secondary" id="more" ${offset + page.rows.length >= page.total || !page.rows.length ? "disabled" : ""}>${esc(store.t("next"))}</button></div>
      <p class="text-meta" id="review-record-help">${esc(store.t("reviewRecordHelp"))}</p>
      ${store.decisionLoadError ? `<div class="notice" id="decision-load-error" role="alert"><strong>${esc(store.t("decisionLoadError"))}</strong><p>${esc(store.decisionLoadError)}</p><button type="button" class="button button-secondary" id="decision-reload">${esc(store.t("decisionReload"))}</button></div>` : ""}
      ${store.decisionConflict ? `<div class="notice" id="decision-conflict" role="alert"><strong>${esc(store.t("decisionConflict"))}</strong><p>${esc(store.t("decisionConflictHelp"))}</p><button type="button" class="button button-secondary" id="decision-reload">${esc(store.t("decisionReload"))}</button></div>` : ""}
      ${store.decisionSaveError ? `<div class="notice" id="decision-save-error" role="alert"><strong>${esc(store.t("decisionSaveError"))}</strong><p>${esc(store.decisionSaveError)}</p><button type="button" class="button button-secondary" id="decision-retry">${esc(store.t("decisionRetry"))}</button></div>` : ""}
      <p>${esc(store.t("reviewHelp"))}</p>
      ${histogramHtml(page)}
      <div class="collection-region" tabindex="0" role="region" aria-label="${esc(store.t("reviewTitle"))}"><table><caption class="text-meta">${esc(store.t("reviewHelp"))}</caption><thead><tr><th scope="col">${esc(store.t("recorded"))}</th><th scope="col">${esc(store.t("leftRecord"))}</th><th scope="col">${esc(store.t("rightRecord"))}</th><th scope="col">${esc(store.t("score"))}</th><th scope="col">${esc(store.t("exact"))}</th></tr></thead>
      <tbody>${page.rows.map((r) => {
            const done = store.checked.has(key(r.left_record_id, r.right_record_id));
            return `<tr class="${done ? "checked" : ""}" data-l="${esc(r.left_record_id)}" data-r="${esc(r.right_record_id)}">
          <td><button type="button" class="button button-check ${done ? "on" : ""}" aria-pressed="${done}" aria-label="${esc(store.t(done ? "recorded" : "reviewed"))}: ${esc(r.left_record_id)} / ${esc(r.right_record_id)}">${esc(store.t(done ? "recorded" : "reviewed"))}</button></td>
          <td><a href="#/compare/${encodeURIComponent(r.left_record_id)}/${encodeURIComponent(r.right_record_id)}">${esc(r.left_record_id)}</a></td>
          <td><a href="#/compare/${encodeURIComponent(r.left_record_id)}/${encodeURIComponent(r.right_record_id)}">${esc(r.right_record_id)}</a></td>
          <td>${(asNumber(r.pair_probability) * 100).toFixed(1)}%</td><td>${asNumber(r.exact_count)}</td></tr>`;
        }).join("") || `<tr><td colspan="5" class="text-meta">${esc(store.t("none"))}</td></tr>`}</tbody></table></div></div></div>`,
        bind: (el, page, scope) => {
            const syncRow = (tr) => {
                const left = tr.dataset.l ?? "";
                const right = tr.dataset.r ?? "";
                const isRecorded = store.checked.has(key(left, right));
                const btn = tr.querySelector(".button-check");
                tr.classList.toggle("checked", isRecorded);
                if (!btn)
                    return;
                btn.classList.toggle("on", isRecorded);
                btn.setAttribute("aria-pressed", String(isRecorded));
                btn.setAttribute("aria-label", `${store.t(isRecorded ? "recorded" : "reviewed")}: ${left} / ${right}`);
                btn.textContent = store.t(isRecorded ? "recorded" : "reviewed");
            };
            el.querySelector("tbody").addEventListener("click", (e) => {
                const btn = e.target.closest(".button-check");
                const tr = e.target.closest("tr");
                if (!btn || !tr)
                    return;
                store.toggleCheckedKey(key(tr.dataset.l ?? "", tr.dataset.r ?? ""));
                syncRow(tr);
                el.querySelector("#count").textContent = store.t("reviewCount", { n: page.total, read: store.checked.size });
            });
            el.querySelector("#reviewer")?.addEventListener("input", (e) => {
                store.reviewer = e.target.value;
                store.write("nayose.reviewer", store.reviewer);
            });
            el.querySelector("#undo")?.addEventListener("click", () => {
                if (!store.undo())
                    return;
                el.querySelectorAll("tbody tr[data-l]").forEach(syncRow);
                el.querySelector("#count").textContent = store.t("reviewCount", { n: page.total, read: store.checked.size });
                void refresh(false);
            });
            const minp = el.querySelector("#minp");
            const syncMinPDisplay = () => {
                const value = clamp01(Number(minp.value) / 100 || 0);
                minp.setAttribute("aria-valuetext", `${Math.round(value * 100)}%`);
                const out = el.querySelector("#minp-val");
                if (out)
                    out.textContent = `${Math.round(value * 100)}%`;
            };
            const applyMinP = () => {
                minP = clamp01(Number(minp.value) / 100 || 0);
                syncMinPDisplay();
                offset = 0;
                syncReviewUrl();
                void refresh(false);
            };
            minp?.addEventListener("input", syncMinPDisplay);
            minp?.addEventListener("change", applyMinP);
            el.querySelector("#page-size")?.addEventListener("change", (e) => {
                pageSize = Number(e.target.value) || 200;
                offset = 0;
                syncReviewUrl();
                void refresh(false);
            });
            el.querySelector("#review-prev")?.addEventListener("click", () => { offset = Math.max(0, offset - pageSize); syncReviewUrl(); void refresh(false); });
            el.querySelector("#more")?.addEventListener("click", () => { offset += pageSize; syncReviewUrl(); void refresh(false); });
            el.querySelector("#filter-clear")?.addEventListener("click", () => {
                filterEntity = "";
                syncReviewUrl();
                void refresh(false);
            });
            el.querySelector("#decision-reload")?.addEventListener("click", async () => {
                const button = el.querySelector("#decision-reload");
                if (button)
                    button.disabled = true;
                try {
                    store.setDecisionSnapshot(await port.loadDecisions(store.run, viewScope?.signal));
                    await refresh(false);
                }
                catch (error) {
                    store.setDecisionLoadError(presentError(error, store.t.bind(store)));
                    if (button)
                        button.disabled = false;
                }
            });
            el.querySelector("#decision-retry")?.addEventListener("click", () => store.retryDecisionSave());
            scope.addEventListener(document, "keydown", (event) => {
                const keyEvent = event;
                const target = keyEvent.target;
                if (!(keyEvent.metaKey || keyEvent.ctrlKey) || keyEvent.key.toLowerCase() !== "z" || target?.matches?.("input,textarea,select,[contenteditable=true]"))
                    return;
                const changed = keyEvent.shiftKey ? store.redo() : store.undo();
                if (!changed)
                    return;
                keyEvent.preventDefault();
                void refresh(false);
            });
        },
    }, viewScope);
    viewScope?.add(disposable(store.on("decisionConflict", () => void refresh(false))));
    viewScope?.add(disposable(store.on("decisionSaveError", () => void refresh(false))));
    viewScope?.add(disposable(store.on("decisionLoadError", () => void refresh(false))));
    await refresh(true);
}
export async function mountConfusion(el, port, store, parentScope) {
    await mountView(el, {
        skeleton: SKELETON(),
        loadingText: store.t("loading"),
        load: (signal) => port.confusion(signal),
        render: (data) => {
            const pairs = data.pairs;
            const count = (id) => asNumber(pairs[id]);
            const total = ["true_positives", "false_positives", "true_negatives", "false_negatives"].reduce((sum, id) => sum + count(id), 0);
            const cell = (id, label, help, tone) => `<td class="${tone}" data-error-cell="${id}"><strong>${formatNumber(count(id), store.locale)}</strong><br><span class="text-meta">${esc(label)}</span><br><span class="text-meta">${total ? `${((count(id) / total) * 100).toFixed(2)}%` : "—"}</span><br><span class="text-meta">${esc(help)}</span></td>`;
            const actions = ["AUTO_LINK", "REVIEW", "NO_MATCH", "INSUFFICIENT_INPUT"];
            const maxCell = Math.max(1, ...data.scenarios.flatMap((s) => actions.map((a) => s.actions[a] ?? 0)));
            const scenRows = data.scenarios.map((s, index) => {
                const actual = s.actual ?? {};
                return `<tr class="scen" data-drill-id="scenario-${index}" data-scenario="${esc(s.scenario)}"><th scope="row"><button type="button" class="button button-secondary" data-scenario-trigger aria-expanded="false" aria-controls="drill">${esc(localizedId(store, s.scenario))}</button></th>${actions.map((a) => { const v = s.actions[a] ?? 0; return `<td class="heat ${heatClass(v, maxCell)}"><strong>${v}</strong><br><span class="text-meta">${esc(store.t("actualCount", { n: actual[a] ?? 0 }))}</span></td>`; }).join("")}</tr>`;
            }).join("");
            const cohorts = (data.error_cohorts ?? []).map((row) => `<tr><th scope="row">${esc(localizedId(store, row.id))}</th><td>${formatNumber(row.count, store.locale)}</td></tr>`).join("");
            const outcomes = (data.query_outcomes ?? []).map((row) => `<tr><th scope="row">${esc(localizedId(store, row.id))}</th><td>${formatNumber(row.count, store.locale)}</td></tr>`).join("");
            return `<div class="panel">${crumb(store, "#/confusion", store.t("errorAnalysis"))}
    <div class="card"><h2>${esc(store.t("errorAnalysis"))}</h2><p class="text-meta">${esc(store.t("errorQuestion"))}</p></div>
    <div class="card"><h3>${esc(store.t("pairMatrix"))} · ${esc(store.t("pairTest"))}</h3><p class="text-meta">${esc(store.t("pairMatrixHelp"))}</p>
      <div class="table-scroll"><table><caption class="text-meta">${esc(store.t("pairMatrix"))}</caption><thead><tr><th></th><th scope="col">${esc(store.t("predictedMatch"))}</th><th scope="col">${esc(store.t("predictedNonMatch"))}</th></tr></thead><tbody>
      <tr><th scope="row">${esc(store.t("actualMatch"))}</th>${cell("true_positives", store.t("true_positives"), store.t("correctSame"), "ok")}${cell("false_negatives", store.t("false_negatives"), store.t("falseNegativeRisk"), "warn")}</tr>
      <tr><th scope="row">${esc(store.t("actualNonMatch"))}</th>${cell("false_positives", store.t("false_positives"), store.t("falsePositiveRisk"), "bad")}${cell("true_negatives", store.t("true_negatives"), store.t("correctDifferent"), "ok")}</tr></tbody></table></div>
      <p class="text-meta">${esc(store.t("threshold"))}: ${pairs.threshold === undefined ? "—" : pairs.threshold.toFixed(4)} · ${esc(store.t("f1"))}: ${pairs.f1 === undefined ? "—" : pairs.f1.toFixed(4)}</p></div>
    <div class="kpi">${(data.query_outcomes ?? []).slice(0, 3).map((row) => `<div class="card"><div class="kpi-value">${formatNumber(row.count, store.locale)}</div><div class="kpi-label">${esc(localizedId(store, row.id))}</div></div>`).join("")}</div>
    <div class="card"><h3>${esc(store.t("errorCohorts"))}</h3><p class="text-meta">${esc(store.t("queryOutcomeHelp"))}</p>${outcomes ? `<table><tbody>${outcomes}</tbody></table>` : `<p class="text-meta">${esc(store.t("noErrorData"))}</p>`}</div>
    <div class="card"><h3>${esc(store.t("scenarioImpact"))}</h3><p class="text-meta">${esc(store.t("scenarioHelp"))}</p><table><caption class="text-meta">${esc(store.t("queryDecisions"))} · ${esc(store.t("expected"))}</caption><thead><tr><th scope="col">${esc(store.t("scenario"))}</th>${actions.map((a) => `<th scope="col">${esc(store.t(a))}</th>`).join("")}</tr></thead><tbody>${scenRows}</tbody></table><div id="drill" role="region" aria-live="polite"></div></div>
    <details class="card"><summary>${esc(store.t("errorCohorts"))} — ${esc(store.t("topTruthStatus"))}</summary>${cohorts ? `<table><tbody>${cohorts}</tbody></table>` : `<p class="text-meta">${esc(store.t("noErrorData"))}</p>`}</details></div>`;
        },
        bind: (el, data) => {
            const drill = el.querySelector("#drill");
            el.querySelectorAll("tr.scen").forEach((tr) => {
                const fire = () => {
                    const scen = str(tr.getAttribute("data-scenario"));
                    const rows = data.decisions[scen] ?? [];
                    tr.querySelector("[data-scenario-trigger]")?.setAttribute("aria-expanded", "true");
                    drill.innerHTML = `<h4 class="muted">${esc(localizedId(store, scen))} — ${esc(store.t("count", { n: rows.length }))}</h4><table><caption class="text-meta">${esc(store.t("queryDecisions"))}</caption><thead><tr><th scope="col">${esc(store.t("query"))}</th><th scope="col">${esc(store.t("action"))}</th><th scope="col">${esc(store.t("topEntity"))}</th><th scope="col">${esc(store.t("score"))}</th><th scope="col">${esc(store.t("top1"))}</th></tr></thead><tbody>${rows.map((r) => `<tr><td>${esc(r.query_id)}</td><td>${esc(store.t(str(r.action)))}</td><td>${esc(r.top_nayose_id)}</td><td>${(asNumber(r.top_probability) * 100).toFixed(1)}%</td><td>${esc(r.identity_top1_correct)}</td></tr>`).join("")}</tbody></table>`;
                };
                tr.addEventListener("click", fire);
            });
        },
    }, parentScope);
}
export async function mountMetrics(el, port, store, parentScope) {
    const metricParams = new URLSearchParams(location.hash.split("?")[1] ?? "");
    let unit = metricParams.get("unit") === "ratio" ? "ratio" : "percent";
    const syncMetricUrl = () => {
        const url = new URL(location.href);
        url.hash = unit === "percent" ? "#/metrics" : "#/metrics?unit=ratio";
        window.history.replaceState(null, "", url);
    };
    const flip = createFlipAnimator(".kpi .card .kpi-value");
    const renderGroup = (data, id, title) => {
        const metrics = data.metric_groups?.[id] ?? [];
        if (!metrics.length)
            return "";
        return `<details class="card"><summary>${esc(store.t(title))}</summary><div class="field-grid">${metrics.map((metric) => `<div><div class="text-meta">${esc(store.t(metric.id))}</div><strong>${esc(formatMetric(metric, unit, store.locale))}</strong></div>`).join("")}</div></details>`;
    };
    const render = (data) => {
        const fmt = (v) => (unit === "percent" ? `${(v * 100).toFixed(1)}%` : v.toFixed(4));
        const assessment = data.assessment;
        const reasons = assessment?.reasons ?? [];
        const reasonKeys = { fragmentation: "assessmentFragmentation", review_load: "assessmentReviewLoad", unresolved_queries: "assessmentUnresolved" };
        const reasonText = reasons.map((reason) => `<li>${esc(store.t(reasonKeys[reason] ?? reason))}</li>`).join("");
        const kpis = data.kpis.map(([label, value]) => `<div class="card"><div class="kpi-value">${fmt(value)}</div><div class="kpi-label">${esc(store.t(metricKey(label)))}</div><div class="text-meta">${esc(store.t(metricHelpKey(label)))}</div>${bar(value)}</div>`).join("");
        const outcomes = (data.outcomes ?? []).map((outcome) => `<div><div class="text-meta">${esc(store.t(outcome.id))}</div><strong>${formatNumber(outcome.count, store.locale)}</strong><span class="text-meta"> · ${(outcome.rate * 100).toFixed(1)}%</span></div>`).join("");
        const status = assessment?.status === "attention" ? "warn" : "ok";
        return `<div class="panel">${crumb(store, "#/metrics", store.t("evaluationOverview"))}
    <div class="card"><h2>${esc(store.t("evaluationOverview"))}</h2><p class="text-meta">${esc(store.t("evaluationQuestion"))}</p><p class="text-meta">${esc(store.t("evaluationRun"))}: ${esc(data.run_id || store.run)}</p>
      <p><span class="chip-state ${status}">${esc(store.t(assessment?.status === "attention" ? "evaluationAttention" : "evaluationReady"))}</span></p>
      <p class="text-meta">${esc(store.t(assessment?.status === "attention" ? "evaluationAttentionHelp" : "evaluationReadyHelp"))}</p>
      ${reasonText ? `<h3>${esc(store.t("evaluationWhy"))}</h3><ul>${reasonText}</ul>` : ""}</div>
    <div class="card toolbar"><label class="text-meta">${esc(store.t("units"))}<select id="met-unit"><option value="percent" ${unit === "percent" ? "selected" : ""}>%</option><option value="ratio" ${unit === "ratio" ? "selected" : ""}>0–1</option></select></label></div>
    <div class="kpi" id="metrics-kpis">${kpis}</div>
    ${outcomes ? `<details class="card" open><summary>${esc(store.t("evaluationOutcomes"))}</summary><div class="field-grid">${outcomes}</div></details>` : ""}
    <div class="card"><h3>${esc(store.t("evaluationNext"))}</h3><p><a href="#/review">${esc(store.t("openReview"))}</a> · <a href="#/confusion">${esc(store.t("openErrors"))}</a> · <a href="#/compare-runs">${esc(store.t("openComparison"))}</a></p></div>
    ${renderGroup(data, "quality", "evaluationQuality")}${renderGroup(data, "workload", "evaluationWorkload")}${renderGroup(data, "structure", "evaluationStructure")}
    <details class="card"><summary>${esc(store.t("evaluationDetails"))} · ${esc(store.t("metrics"))}</summary><div class="text-meta">${esc(store.t("edgesSummary", { n: data.extras.edges ?? 0, rate: `${((data.extras.singleton ?? 0) * 100).toFixed(1)}%` }))}</div></details></div>`;
    };
    await mountView(el, {
        skeleton: SKELETON(),
        loadingText: store.t("loading"),
        load: (signal) => port.metrics(signal),
        render,
        bind: (view, data) => {
            const bindUnit = () => {
                view.querySelector("#met-unit")?.addEventListener("change", (e) => {
                    flip.capture(view);
                    unit = e.target.value;
                    syncMetricUrl();
                    view.innerHTML = render(data);
                    flip.play(view);
                    bindUnit();
                });
            };
            bindUnit();
        },
    }, parentScope);
}
export async function mountEntities(el, port, store, scope) {
    await mountEntityExplorer(el, port, store, scope);
}
export async function mountEntity(el, port, store, id, scope) {
    await mountEntityDetail(el, port, store, id, scope);
}
export async function mountCompare(el, port, store, l, r, parentScope) {
    await mountView(el, {
        skeleton: SKELETON(),
        loadingText: store.t("loading"),
        load: (signal) => port.compare(l, r, signal),
        render: (rows) => {
            const left = rows.find((x) => str(x.record_id) === l);
            const right = rows.find((x) => str(x.record_id) === r);
            if (!left || !right) {
                return `<div class="panel"><div class="empty">${esc(store.t("notFound"))}</div></div>`;
            }
            const fields = diffFields(left);
            const side = (rec, other) => `<table><tbody>${fields.map((f) => `<tr><th scope="row">${esc(f)}</th><td class="${str(rec[f]) !== str(other[f]) ? "diff-hi" : ""}">${esc(rec[f])}</td></tr>`).join("")}</tbody></table>`;
            return `<div class="panel">${crumb(store, "#/review", store.t("review"))}
    <div class="diff" data-panel-split="compare">
      <div class="card"><h3>${esc(l)}</h3>${side(left, right)}</div>
      <div class="panel-resizer sash" data-panel-resizer="compare" role="separator" aria-orientation="vertical" aria-label="${esc(store.t("resizePanel"))}" aria-valuemin="${COMPARE_SPLIT_MIN}" aria-valuemax="${COMPARE_SPLIT_MAX}" aria-valuenow="${COMPARE_SPLIT_DEFAULT}" tabindex="0"></div>
      <div class="card"><h3>${esc(r)}</h3>${side(right, left)}</div>
    </div><p class="text-meta">${esc(store.t("diffHelp"))}</p></div>`;
        },
        bind: (view, _rows, scope) => {
            scope.add(bindPanelSplit(view, store, {
                id: "compare",
                cssVariable: "--compare-left-width",
                defaultWidth: COMPARE_SPLIT_DEFAULT,
                minWidth: COMPARE_SPLIT_MIN,
                maxWidth: COMPARE_SPLIT_MAX,
                pointerSide: "left",
                mobileQuery: "(max-width: 980px)",
            }));
        },
    }, parentScope);
}
export async function mountCompareRuns(el, port, store, parentScope) {
    const runs = store.runs.map((r) => r.id);
    const compareParams = new URLSearchParams(location.hash.split("?")[1] ?? "");
    const defaultBaseline = runs.find((r) => r !== store.run) ?? store.run;
    const a = runs.includes(compareParams.get("a") ?? "") ? compareParams.get("a") : defaultBaseline;
    const b = runs.includes(compareParams.get("b") ?? "") ? compareParams.get("b") : store.run;
    const syncCompareUrl = (left, right) => {
        const params = new URLSearchParams({ a: left, b: right });
        window.history.replaceState(null, "", `#/compare-runs?${params}`);
    };
    const opts = (sel) => runs.map((r) => `<option value="${esc(r)}" ${r === sel ? "selected" : ""}>${esc(r)}</option>`).join("");
    let request = 0;
    const go = async () => {
        const currentRequest = ++request;
        const output = el.querySelector("#out");
        if (!output)
            return;
        try {
            await mountView(output, {
                skeleton: SKELETON(2),
                loadingText: store.t("loading"),
                load: async (signal) => {
                    const runA = el.querySelector("#runA").value;
                    const runB = el.querySelector("#runB").value;
                    return Promise.all([port.metricsFor(runA, signal), port.metricsFor(runB, signal)]);
                },
                render: ([mA, mB]) => {
                    const byA = new Map(mA.kpis), byB = new Map(mB.kpis);
                    const isWorkload = (label) => ["review", "no-match"].includes(label);
                    let improved = 0, regressed = 0;
                    const rows = [...new Set([...byA.keys(), ...byB.keys()])].map((label) => {
                        const va = byA.get(label) ?? 0, vb = byB.get(label) ?? 0;
                        const delta = vb - va;
                        const meaningful = Math.abs(delta) > 0.005;
                        const good = isWorkload(label) ? delta < 0 : delta > 0;
                        if (meaningful && good)
                            improved += 1;
                        if (meaningful && !good)
                            regressed += 1;
                        const cls = !meaningful ? "blank" : good ? "ok" : "bad";
                        return `<tr><th scope="row">${esc(store.t(metricKey(label)))}</th><td>${(va * 100).toFixed(1)}%</td><td>${(vb * 100).toFixed(1)}%</td>
              <td class="${cls}">${delta >= 0 ? "+" : ""}${(delta * 100).toFixed(1)}%</td></tr>`;
                    }).join("");
                    const runA = el.querySelector("#runA").value;
                    const runB = el.querySelector("#runB").value;
                    const verdict = regressed > improved ? "comparisonWorse" : improved > regressed ? "comparisonBetter" : "comparisonSame";
                    const verdictClass = regressed > improved ? "bad" : improved > regressed ? "ok" : "blank";
                    return `<div class="card"><h2>${esc(store.t("releaseComparison"))}</h2><p class="text-meta">${esc(store.t("promoteQuestion"))}</p><p><span class="chip-state ${verdictClass}">${esc(store.t(verdict))}</span></p><p class="text-meta">${esc(store.t("comparisonHelpNew"))}</p><p class="text-meta">${esc(store.t("compareHelp"))}</p></div>
            <div class="card"><h3>${esc(store.t("baselineRun"))} → ${esc(store.t("currentRun"))}</h3><p class="muted">${esc(runA)} → ${esc(runB)}</p>
            <table><caption class="text-meta">${esc(store.t("compare"))}</caption><thead><tr><th scope="col">${esc(store.t("metrics"))}</th><th scope="col">${esc(store.t("baselineRun"))}</th><th scope="col">${esc(store.t("currentRun"))}</th><th scope="col">${esc(store.t("change"))}</th></tr></thead><tbody>${rows}</tbody></table></div>`;
                },
            }, parentScope);
        }
        catch (err) {
            if (parentScope?.isDisposed || currentRequest !== request)
                return;
            const message = presentError(err, store.t.bind(store));
            const currentOutput = el.querySelector("#out");
            if (currentOutput)
                currentOutput.innerHTML = `<p class="text-meta">${esc(store.t("error"))}: ${esc(message)}</p>`;
        }
    };
    await mountView(el, {
        load: async (_signal) => undefined,
        render: () => `<div class="panel">${crumb(store, "#/compare-runs", store.t("releaseComparison"))}
    <div class="card"><h2>${esc(store.t("releaseComparison"))}</h2><p class="text-meta">${esc(store.t("promoteQuestion"))}</p><p class="text-meta">${esc(store.t("comparisonHelpNew"))}</p><p class="text-meta">${esc(store.t("compareHelp"))}</p><div class="toolbar">
      <label class="text-meta">${esc(store.t("baselineRun"))}<select id="runA" aria-label="${esc(store.t("baselineRun"))}">${opts(a)}</select></label>
      <label class="text-meta">${esc(store.t("currentRun"))}<select id="runB" aria-label="${esc(store.t("currentRun"))}">${opts(b)}</select></label>
      <button type="button" class="button button-secondary" id="go">${esc(store.t("compare"))}</button></div>
      <div id="out" role="region" aria-live="polite"><div class="text-meta">${esc(store.t("compareHelp"))}</div></div></div></div>`,
        bind: (el) => {
            const updateUrl = () => syncCompareUrl(el.querySelector("#runA").value, el.querySelector("#runB").value);
            el.querySelector("#runA")?.addEventListener("change", updateUrl);
            el.querySelector("#runB")?.addEventListener("change", updateUrl);
            el.querySelector("#go").addEventListener("click", () => { updateUrl(); void go(); });
        },
    }, parentScope);
}
