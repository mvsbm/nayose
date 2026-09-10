import { mountMatchingSearch } from "./match-view.js";
import { mountEntityExplorer } from "./entity-explorer.js";
import { mountEntityDetail } from "./entity-view.js";
import { asNumber, checkedKey, clamp01, diffFields } from "./core.js";
import { esc, str } from "./schema.js";
import { createFlipAnimator } from "./shared.js";
import { createViewScope } from "./lifecycle.js";
import { bindPanelSplit } from "./panel-layout.js";
const COMPARE_SPLIT_DEFAULT = 440;
const COMPARE_SPLIT_MIN = 280;
const COMPARE_SPLIT_MAX = 720;
const SKELETON = (k = 3) => `<div class="skel">${Array.from({ length: k }, () => '<div class="card" style="height:3.2rem"></div>').join("")}</div>`;
const crumb = (store, ...links) => `<nav class="crumbs" aria-label="${esc(store.t("crumbs"))}">${links.map((l, i) => (i % 2 ? `<a href="${esc(links[i - 1])}">${esc(l)}</a>` : l)).join(" / ")}</nav>`;
const bar = (v) => `<span class="meter"><i style="width:${clamp01(v) * 100}%"></i></span>`;
const mountVersions = new WeakMap();
const mountScopes = new WeakMap();
async function mountView(el, spec) {
    const version = (mountVersions.get(el) ?? 0) + 1;
    mountVersions.set(el, version);
    mountScopes.get(el)?.dispose();
    const scope = createViewScope(el);
    mountScopes.set(el, scope);
    if (spec.skeleton)
        el.innerHTML = spec.skeleton;
    const data = await spec.load();
    if (scope.isDisposed || mountVersions.get(el) !== version)
        return;
    el.innerHTML = spec.render(data);
    spec.bind?.(el, data, scope);
}
export async function mountSearch(el, port, store, scope) {
    await mountMatchingSearch(el, port, store, scope);
}
export async function mountReview(el, port, store) {
    let offset = 0;
    let minP = 0;
    let pageSize = 200;
    let filterEntity = new URLSearchParams(location.hash.split("?")[1] ?? "").get("nayose") ?? "";
    const key = (l, r) => checkedKey(store.run, l, r);
    const hiddenBelow = (page) => store.t("hiddenBelow", { n: Math.max(0, page.total - page.rows.length + offset), m: page.total });
    const histogramHtml = (page) => {
        const bucket = (b) => `data-lo="${b[0]}" title="${(b[0] * 100).toFixed(0)}%–${((b[0] + 0.05) * 100).toFixed(0)}%: ${b[1]}"`;
        const max = Math.max(1, ...page.histogram.map(([, c]) => c));
        const bars = page.histogram
            .map((b) => `<div class="hist-bar" style="height:${Math.max(2, Math.round((b[1] / max) * 100))}%" ${bucket(b)}></div>`)
            .join("");
        const mark = (p, cls, title) => `<div class="hist-mark ${cls}" style="left:${(p * 1000) / 10}%" title="${esc(title)}"></div>`;
        const pairMark = page.pair_auto_threshold
            ? mark(page.pair_auto_threshold, "", store.t("pairAutoMark", { p: (page.pair_auto_threshold * 100).toFixed(1) + "%" }))
            : "";
        return `<div class="card"><h3>${esc(store.t("scoreBands"))}</h3>
      <div class="hist" aria-hidden="true">${bars}
        ${mark(minP, "", "minP")}${pairMark}</div>
      <p class="members">${hiddenBelow(page)}</p></div>`;
    };
    const refresh = (initial) => mountView(el, {
        skeleton: initial ? SKELETON() : undefined,
        load: () => port.review(minP, offset, pageSize, filterEntity),
        render: (page) => `<div class="panel">${crumb(store, "#/review", store.t("review"))}
      <div class="card"><div class="toolbar">
        <h3 style="margin:0">${esc(store.t("reviewTitle"))}</h3>
        <label class="members">${esc(store.t("scoreFilter"))}
          <input type="range" id="minp" min="0" max="100" step="1" value="${Math.round(minP * 100)}" aria-valuetext="${Math.round(minP * 100)}%"></label>
        <output class="members" for="minp" id="minp-val">${Math.round(minP * 100)}%</output>
        <label class="members">${esc(store.t("pageSize"))}<select id="page-size" aria-label="page size">
          ${[25, 50, 100, 200, 500].map(n => `<option ${n === pageSize ? "selected" : ""}>${n}</option>`).join("")}
        </select></label>
        <span class="members" id="count" role="status" aria-live="polite">${esc(store.t("reviewCount", { n: page.total, read: store.checked.size }))}</span>
        ${filterEntity ? `<button type="button" class="chip-state warn" id="filter-clear" title="${esc(filterEntity)}">${esc(store.t("filterByEntity", { id: filterEntity }))} ✕</button>` : ""}
        <label class="members">${esc(store.t("reviewer"))}<input id="reviewer" class="bar" type="text" size="10" value="${esc(store.reviewer)}"></label>
        <button type="button" class="more" id="undo">${esc(store.t("undo"))}</button>
        <button type="button" class="more" id="more">${esc(store.t("next200"))}</button></div>
      <p class="members" id="review-record-help">${esc(store.t("reviewRecordHelp"))}</p>
      <p>${esc(store.t("reviewHelp"))}</p>
      ${histogramHtml(page)}
      <table><caption class="members">${esc(store.t("reviewHelp"))}</caption><thead><tr><th>${esc(store.t("recorded"))}</th><th>${esc(store.t("leftRecord"))}</th><th>${esc(store.t("rightRecord"))}</th><th>${esc(store.t("score"))}</th><th>exact</th></tr></thead>
      <tbody>${page.rows.map((r) => {
            const done = store.checked.has(key(r.left_record_id, r.right_record_id));
            return `<tr class="${done ? "checked" : ""}" data-l="${esc(r.left_record_id)}" data-r="${esc(r.right_record_id)}">
          <td><button type="button" class="check ${done ? "on" : ""}" aria-pressed="${done}" aria-label="${esc(store.t(done ? "recorded" : "reviewed"))}: ${esc(r.left_record_id)} / ${esc(r.right_record_id)}">${esc(store.t(done ? "recorded" : "reviewed"))}</button></td>
          <td><a href="#/compare/${esc(r.left_record_id)}/${esc(r.right_record_id)}">${esc(r.left_record_id)}</a></td>
          <td><a href="#/compare/${esc(r.left_record_id)}/${esc(r.right_record_id)}">${esc(r.right_record_id)}</a></td>
          <td>${(asNumber(r.pair_probability) * 100).toFixed(1)}%</td><td>${asNumber(r.exact_count)}</td></tr>`;
        }).join("") || `<tr><td colspan="5" class="members">${esc(store.t("none"))}</td></tr>`}</tbody></table></div></div>`,
        bind: (el, page) => {
            const syncRow = (tr) => {
                const left = tr.dataset.l ?? "";
                const right = tr.dataset.r ?? "";
                const isRecorded = store.checked.has(key(left, right));
                const btn = tr.querySelector("button.check");
                tr.classList.toggle("checked", isRecorded);
                if (!btn)
                    return;
                btn.classList.toggle("on", isRecorded);
                btn.setAttribute("aria-pressed", String(isRecorded));
                btn.setAttribute("aria-label", `${store.t(isRecorded ? "recorded" : "reviewed")}: ${left} / ${right}`);
                btn.textContent = store.t(isRecorded ? "recorded" : "reviewed");
            };
            el.querySelector("tbody").addEventListener("click", (e) => {
                const btn = e.target.closest("button.check");
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
            const applyMinP = () => {
                minP = clamp01(Number(minp.value) / 100 || 0);
                minp.setAttribute("aria-valuetext", `${Math.round(minP * 100)}%`);
                const out = el.querySelector("#minp-val");
                if (out)
                    out.textContent = `${Math.round(minP * 100)}%`;
                offset = 0;
                void refresh(false);
            };
            minp?.addEventListener("input", applyMinP);
            minp?.addEventListener("change", applyMinP);
            el.querySelector("#page-size")?.addEventListener("change", (e) => {
                pageSize = Number(e.target.value) || 200;
                offset = 0;
                void refresh(false);
            });
            el.querySelector("#more")?.addEventListener("click", () => { offset += pageSize; void refresh(false); });
            el.querySelector("#filter-clear")?.addEventListener("click", () => {
                filterEntity = "";
                location.hash = "#/review";
            });
        },
    });
    await refresh(true);
}
export async function mountConfusion(el, port, store) {
    await mountView(el, {
        skeleton: SKELETON(),
        load: () => port.confusion(),
        render: (data) => {
            const actions = ["AUTO_LINK", "REVIEW", "NO_MATCH", "INSUFFICIENT_INPUT"];
            const maxCell = Math.max(1, ...data.scenarios.flatMap((s) => actions.map((a) => s.actions[a] ?? 0)));
            const scenRows = data.scenarios
                .map((s) => `<tr class="scen" tabindex="0" data-scenario="${esc(s.scenario)}"><th scope="row">${esc(s.scenario)}</th>${actions
                .map((a) => { const v = s.actions[a] ?? 0; return `<td class="heat" style="--heat:${Math.round((v / maxCell) * 50)}%">${v}</td>`; })
                .join("")}</tr>`)
                .join("");
            const pairRows = ["true_positives", "false_positives", "true_negatives", "false_negatives", "precision", "recall", "specificity", "f1", "auc"]
                .filter((m) => m in data.pairs)
                .map((m) => `<tr><th scope="row">${esc(m)}</th><td>${Number(data.pairs[m]).toFixed(4)}</td></tr>`)
                .join("");
            return `<div class="panel">${crumb(store, "#/confusion", store.t("confusion"))}
    <div class="card"><h3>${esc(store.t("pairTest"))}</h3><table><tbody>${pairRows}</tbody></table></div>
    <div class="card"><h3>${esc(store.t("queryDecisions"))}</h3>
      <table><thead><tr><th>scenario</th>${actions.map((a) => `<th>${esc(a)}</th>`).join("")}</tr></thead>
      <tbody>${scenRows}</tbody></table><div id="drill" role="region" aria-live="polite"></div></div></div>`;
        },
        bind: (el, data) => {
            const drill = el.querySelector("#drill");
            el.querySelectorAll("tr.scen").forEach((tr) => {
                const fire = () => {
                    const scen = str(tr.getAttribute("data-scenario"));
                    const rows = data.decisions[scen] ?? [];
                    drill.innerHTML = `<h4 class="muted">${esc(scen)} — ${esc(store.t("count", { n: rows.length }))}</h4>
            <table><thead><tr><th>query</th><th>action</th><th>top entity</th><th>${esc(store.t("score"))}</th><th>top1</th></tr></thead>
            <tbody>${rows.map((r) => `<tr><td>${esc(r.query_id)}</td><td>${esc(r.action)}</td><td>${esc(r.top_nayose_id)}</td><td>${(asNumber(r.top_probability) * 100).toFixed(1)}%</td><td>${esc(r.identity_top1_correct)}</td></tr>`).join("")}</tbody></table>`;
                };
                tr.addEventListener("click", fire);
                tr.addEventListener("keydown", (ev) => {
                    const e = ev;
                    if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        fire();
                    }
                });
            });
        },
    });
}
export async function mountMetrics(el, port, store) {
    // F: metric unit toggle (% vs ratio) — display only
    let unit = "percent";
    const flip = createFlipAnimator(".kpi .card .v");
    const render = (data) => {
        const fmt = (v) => (unit === "percent" ? `${(v * 100).toFixed(1)}%` : v.toFixed(4));
        return `<div class="panel">${crumb(store, "#/metrics", store.t("metrics"))}
    <div class="card toolbar"><label class="members">${esc(store.t("units"))}<select id="met-unit"><option value="percent" ${unit === "percent" ? "selected" : ""}>%</option><option value="ratio" ${unit === "ratio" ? "selected" : ""}>0–1</option></select></label></div>
    <div class="kpi">${data.kpis.map(([l, v]) => `<div class="card"><div class="v">${fmt(v)}</div><div class="l">${esc(l)}</div>${bar(v)}</div>`).join("")}</div>
    <div class="card"><h3>${esc(store.t("entities"))}</h3><div class="v" style="font-size:1.4rem">${(data.extras.entities ?? 0).toLocaleString()}</div>
      <div class="members">${esc(store.t("edgesSummary", { n: data.extras.edges ?? 0, rate: `${((data.extras.singleton ?? 0) * 100).toFixed(1)}%` }))}</div></div></div>`;
    };
    await mountView(el, {
        skeleton: SKELETON(),
        load: () => port.metrics(),
        render,
        bind: (view, data) => {
            const bindUnit = () => {
                view.querySelector("#met-unit")?.addEventListener("change", (e) => {
                    flip.capture(view);
                    unit = e.target.value;
                    view.innerHTML = render(data);
                    flip.play(view);
                    bindUnit();
                });
            };
            bindUnit();
        },
    });
}
export async function mountEntities(el, port, store, scope) {
    await mountEntityExplorer(el, port, store, scope);
}
export async function mountEntity(el, port, store, id, scope) {
    await mountEntityDetail(el, port, store, id, scope);
}
export async function mountCompare(el, port, store, l, r) {
    await mountView(el, {
        skeleton: SKELETON(),
        load: () => port.compare(l, r),
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
      <div class="panel-resizer" data-panel-resizer="compare" role="separator" aria-orientation="vertical" aria-label="${esc(store.t("resizePanel"))}" aria-valuemin="${COMPARE_SPLIT_MIN}" aria-valuemax="${COMPARE_SPLIT_MAX}" aria-valuenow="${COMPARE_SPLIT_DEFAULT}" tabindex="0"></div>
      <div class="card"><h3>${esc(r)}</h3>${side(right, left)}</div>
    </div><p class="members">${esc(store.t("diffHelp"))}</p></div>`;
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
    });
}
export async function mountCompareRuns(el, port, store) {
    const runs = store.runs.map((r) => r.id);
    const a = store.run;
    const b = runs.find((r) => r !== a) ?? a;
    const opts = (sel) => runs.map((r) => `<option value="${esc(r)}" ${r === sel ? "selected" : ""}>${esc(r)}</option>`).join("");
    const go = async () => {
        try {
            await mountView(el.querySelector("#out"), {
                skeleton: SKELETON(2),
                load: async () => {
                    const runA = el.querySelector("#runA").value;
                    const runB = el.querySelector("#runB").value;
                    return Promise.all([port.metricsFor(runA), port.metricsFor(runB)]);
                },
                render: ([mA, mB]) => {
                    const byA = new Map(mA.kpis), byB = new Map(mB.kpis);
                    const rows = [...new Set([...byA.keys(), ...byB.keys()])].map((label) => {
                        const va = byA.get(label) ?? 0, vb = byB.get(label) ?? 0;
                        const delta = vb - va;
                        const cls = Math.abs(delta) > 0.005 ? (delta > 0 ? "ok" : "bad") : "blank";
                        return `<tr><th scope="row">${esc(label)}</th><td>${(va * 100).toFixed(1)}%</td><td>${(vb * 100).toFixed(1)}%</td>
              <td class="${cls}">${delta >= 0 ? "+" : ""}${(delta * 100).toFixed(1)}%</td></tr>`;
                    }).join("");
                    const runA = el.querySelector("#runA").value;
                    const runB = el.querySelector("#runB").value;
                    return `<h4 class="muted">${esc(runA)} → ${esc(runB)}</h4>
            <table><thead><tr><th>${esc(store.t("metrics"))}</th><th>A</th><th>B</th><th>Δ</th></tr></thead><tbody>${rows}</tbody></table>`;
                },
            });
        }
        catch (err) {
            el.querySelector("#out").innerHTML = `<p class="members">${esc(store.t("error"))}: ${esc(String(err?.message ?? err))}</p>`;
        }
    };
    await mountView(el, {
        load: async () => undefined,
        render: () => `<div class="panel">${crumb(store, "#/compare-runs", store.t("compare"))}
    <div class="card"><div class="toolbar">
      <label class="members">run A<select id="runA">${opts(a)}</select></label>
      <label class="members">run B<select id="runB">${opts(b)}</select></label>
      <button type="button" class="more" id="go">${esc(store.t("compare"))}</button></div>
      <div id="out" role="region" aria-live="polite"><div class="members">${esc(store.t("compareHelp"))}</div></div></div></div>`,
        bind: (el) => {
            el.querySelector("#go").addEventListener("click", () => void go());
        },
    });
}
