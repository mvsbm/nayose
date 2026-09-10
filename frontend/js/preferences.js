const STORAGE_KEY = "nayose.preferences";
const VERSION = 1;
const DEFAULTS = {
    theme: "auto",
    density: "comfortable",
    navigationCollapsed: false,
    navigationWidth: 256,
    locale: "ja",
};
function isTheme(value) {
    return value === "light" || value === "dark" || value === "auto";
}
function isDensity(value) {
    return value === "comfortable" || value === "compact";
}
function isLocale(value) {
    return value === "ja" || value === "en";
}
function boundedWidth(value) {
    if (value === null || value === undefined || value === "")
        return DEFAULTS.navigationWidth;
    const width = Number(value);
    return Number.isFinite(width) ? Math.min(420, Math.max(220, Math.round(width))) : DEFAULTS.navigationWidth;
}
function decode(value) {
    if (!value || typeof value !== "object" || Array.isArray(value))
        return null;
    const candidate = value;
    return {
        theme: isTheme(candidate.theme) ? candidate.theme : DEFAULTS.theme,
        density: isDensity(candidate.density) ? candidate.density : DEFAULTS.density,
        navigationCollapsed: candidate.navigationCollapsed === true,
        navigationWidth: boundedWidth(candidate.navigationWidth),
        locale: isLocale(candidate.locale) ? candidate.locale : DEFAULTS.locale,
    };
}
function readJson(storage, key) {
    const raw = storage.read(key);
    if (!raw)
        return null;
    try {
        return JSON.parse(raw);
    }
    catch {
        return null;
    }
}
export function readPreferences(storage) {
    const stored = readJson(storage, STORAGE_KEY);
    if (stored && typeof stored === "object" && !Array.isArray(stored)) {
        const envelope = stored;
        if (envelope.version === VERSION) {
            const decoded = decode(envelope.data);
            if (decoded)
                return decoded;
        }
    }
    const legacy = decode({
        theme: storage.read("nayose.theme"),
        density: storage.read("nayose.density"),
        navigationCollapsed: storage.read("nayose.navigationCollapsed") === "true",
        navigationWidth: storage.read("nayose.navigationWidth"),
        locale: storage.read("nayose.locale"),
    });
    return legacy ?? { ...DEFAULTS };
}
export function writePreferences(storage, preferences) {
    const value = decode(preferences) ?? { ...DEFAULTS };
    storage.write(STORAGE_KEY, JSON.stringify({ version: VERSION, data: value }));
    storage.write("nayose.theme", value.theme);
    storage.write("nayose.density", value.density);
    storage.write("nayose.navigationCollapsed", String(value.navigationCollapsed));
    storage.write("nayose.navigationWidth", String(value.navigationWidth));
    storage.write("nayose.locale", value.locale);
}
