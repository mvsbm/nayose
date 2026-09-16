const percent = (value) => `${Math.round(value * 100)}%`;
function localized(store, item, kind) {
    if (kind === "label")
        return store.locale === "ja" ? item.label_ja : item.label_en;
    return store.locale === "ja" ? item.description_ja : item.description_en;
}
export function syncControlOutputs(el, catalog, controls, store) {
    el.querySelectorAll("[data-event-value]").forEach(output => {
        output.value = percent(controls.event_rates[output.dataset.eventValue ?? ""] ?? 0);
    });
    el.querySelectorAll("[data-band-value]").forEach(output => {
        output.value = `${(controls.band_scale[output.dataset.bandValue ?? ""] ?? 0).toFixed(2)}×`;
    });
    el.querySelectorAll("[data-missing-value]").forEach(output => {
        output.value = percent(controls.missing_rates[output.dataset.missingValue ?? ""] ?? 0);
    });
    el.querySelectorAll("[data-availability-value]").forEach(output => {
        output.value = percent(controls.field_availability[output.dataset.channel ?? ""]?.[output.dataset.field ?? ""] ?? 1);
    });
    el.querySelectorAll("[data-query-value]").forEach(output => {
        output.value = percent(controls.query_mix[output.dataset.queryValue ?? ""] ?? 0);
    });
    const values = {
        person_share: controls.person_share,
        query_share: controls.query_share,
        twin_rate: controls.twin_rate,
        poor: controls.poor ?? 0,
        catastrophic: controls.catastrophic ?? 0,
        stale_rate: controls.stale_rate ?? 0,
    };
    el.querySelectorAll("[data-control-output]").forEach(output => {
        output.value = percent(values[output.dataset.controlOutput ?? ""] ?? 0);
    });
    const total = Object.values(controls.query_mix).reduce((sum, value) => sum + value, 0);
    const queryTotal = el.querySelector("#query-total");
    if (queryTotal)
        queryTotal.textContent = `${store.t("queryTotal")}: ${percent(total)}${Math.abs(total - 1) < 0.001 ? "" : ` · ${store.t("queryTotalInvalid")}`}`;
    const quality = el.querySelector("#gen-quality-summary");
    if (quality) {
        const poor = controls.poor ?? 0;
        const catastrophic = controls.catastrophic ?? 0;
        quality.textContent = `${store.t("qualityAverage")}: ${percent(1 - poor)} · ${store.t("qualityPoor")}: ${percent(poor - catastrophic)} · ${store.t("qualityCatastrophic")}: ${percent(catastrophic)}`;
    }
    const profile = catalog.profiles.find(item => item.id === controls.profile);
    const profileInfo = el.querySelector("#gen-profile-info");
    if (profileInfo && profile)
        profileInfo.textContent = localized(store, profile, "description");
}
export function cloneGenerationControls(controls) {
    return JSON.parse(JSON.stringify(controls));
}
export function generationControlsHash(controls) {
    return JSON.stringify(controls);
}
function setValue(el, selector, value) {
    const input = el.querySelector(selector);
    if (input)
        input.value = String(value);
}
export function resetGenerationControls(el, catalog, controls, defaults, store, onChange) {
    Object.assign(controls, cloneGenerationControls(defaults));
    setValue(el, "#gen-profile", controls.profile);
    setValue(el, "#gen-diff", controls.difficulty);
    setValue(el, "#gen-seed", controls.seed);
    setValue(el, "#gen-n", controls.n_parties);
    setValue(el, "#gen-mean", controls.mean_records ?? "");
    setValue(el, "#gen-agencies", controls.n_agencies);
    setValue(el, "#gen-person-share", Math.round(controls.person_share * 100));
    setValue(el, "#gen-query-share", Math.round(controls.query_share * 100));
    setValue(el, "#gen-twin-rate", Math.round(controls.twin_rate * 100));
    setValue(el, "#gen-poor", Math.round((controls.poor ?? 0) * 100));
    setValue(el, "#gen-catastrophic", Math.round((controls.catastrophic ?? 0) * 100));
    setValue(el, "#gen-snapshot", controls.snapshot_date);
    setValue(el, "#gen-query-date", controls.query_date);
    setValue(el, "#gen-stale-rate", Math.round((controls.stale_rate ?? 0) * 100));
    el.querySelectorAll("[data-event-rate]").forEach(input => { input.value = String(Math.round((controls.event_rates[input.dataset.eventRate ?? ""] ?? 0) * 100)); });
    el.querySelectorAll("[data-band-scale]").forEach(input => { input.value = String(controls.band_scale[input.dataset.bandScale ?? ""] ?? 0); });
    el.querySelectorAll("[data-missing-rate]").forEach(input => { input.value = String(Math.round((controls.missing_rates[input.dataset.missingRate ?? ""] ?? 0) * 100)); });
    el.querySelectorAll("[data-availability]").forEach(input => { input.value = String(Math.round((controls.field_availability[input.dataset.channel ?? ""]?.[input.dataset.field ?? ""] ?? 1) * 100)); });
    el.querySelectorAll("[data-query-mix]").forEach(input => { input.value = String(Math.round((controls.query_mix[input.dataset.queryMix ?? ""] ?? 0) * 100)); });
    syncControlOutputs(el, catalog, controls, store);
    onChange();
}
export function bindGenerationControls(el, catalog, controls, store, onChange) {
    const schedule = (change) => {
        change();
        syncControlOutputs(el, catalog, controls, store);
        onChange();
    };
    el.querySelector("#gen-profile")?.addEventListener("change", event => {
        const profile = catalog.profiles.find(item => item.id === event.target.value);
        if (!profile)
            return;
        schedule(() => {
            controls.profile = profile.id;
            controls.n_parties = 0;
            controls.mean_records = profile.mean_records;
            const parties = el.querySelector("#gen-n");
            const mean = el.querySelector("#gen-mean");
            if (parties)
                parties.value = "0";
            if (mean)
                mean.value = String(profile.mean_records);
        });
    });
    el.querySelector("#gen-diff")?.addEventListener("change", event => {
        const difficulty = catalog.difficulties.find(item => item.id === event.target.value);
        if (!difficulty)
            return;
        schedule(() => {
            controls.difficulty = difficulty.id;
            controls.poor = difficulty.poor;
            controls.catastrophic = difficulty.catastrophic;
            const poor = el.querySelector("#gen-poor");
            const catastrophic = el.querySelector("#gen-catastrophic");
            if (poor)
                poor.value = String(Math.round(difficulty.poor * 100));
            if (catastrophic)
                catastrophic.value = String(Math.round(difficulty.catastrophic * 100));
        });
    });
    const bindNumber = (id, change) => {
        el.querySelector(id)?.addEventListener("input", event => {
            const value = Number(event.target.value);
            if (Number.isFinite(value))
                schedule(() => change(value));
        });
    };
    bindNumber("#gen-seed", value => { controls.seed = Math.max(0, Math.floor(value)); });
    bindNumber("#gen-n", value => { controls.n_parties = Math.max(0, Math.floor(value)); });
    bindNumber("#gen-mean", value => { controls.mean_records = value; });
    bindNumber("#gen-agencies", value => { controls.n_agencies = Math.floor(value); });
    bindNumber("#gen-person-share", value => { controls.person_share = value / 100; });
    bindNumber("#gen-query-share", value => { controls.query_share = value / 100; });
    bindNumber("#gen-twin-rate", value => { controls.twin_rate = value / 100; });
    bindNumber("#gen-poor", value => { controls.poor = value / 100; if ((controls.catastrophic ?? 0) > controls.poor)
        controls.catastrophic = controls.poor; });
    bindNumber("#gen-catastrophic", value => { controls.catastrophic = Math.min(value / 100, controls.poor ?? 0); });
    el.querySelector("#gen-snapshot")?.addEventListener("change", event => schedule(() => { controls.snapshot_date = event.target.value; }));
    el.querySelector("#gen-query-date")?.addEventListener("change", event => schedule(() => { controls.query_date = event.target.value; }));
    el.querySelectorAll("[data-event-rate]").forEach(input => input.addEventListener("input", event => {
        const target = event.target;
        schedule(() => { controls.event_rates[target.dataset.eventRate] = Number(target.value) / 100; });
    }));
    el.querySelectorAll("[data-band-scale]").forEach(input => input.addEventListener("input", event => {
        const target = event.target;
        schedule(() => { controls.band_scale[target.dataset.bandScale] = Number(target.value); });
    }));
    el.querySelectorAll("[data-missing-rate]").forEach(input => input.addEventListener("input", event => {
        const target = event.target;
        schedule(() => { controls.missing_rates[target.dataset.missingRate] = Number(target.value) / 100; });
    }));
    el.querySelectorAll("[data-availability]").forEach(input => input.addEventListener("input", event => {
        const target = event.target;
        schedule(() => { controls.field_availability[target.dataset.channel][target.dataset.field] = Number(target.value) / 100; });
    }));
    el.querySelector("#gen-stale-rate")?.addEventListener("input", event => schedule(() => { controls.stale_rate = Number(event.target.value) / 100; }));
    el.querySelectorAll("[data-query-mix]").forEach(input => input.addEventListener("input", event => {
        const target = event.target;
        schedule(() => { controls.query_mix[target.dataset.queryMix] = Number(target.value) / 100; });
    }));
    syncControlOutputs(el, catalog, controls, store);
}
