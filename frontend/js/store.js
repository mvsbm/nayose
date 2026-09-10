import { translate } from "./i18n.js";
import { MatchingState } from "./matching-state.js";
import { readPreferences, writePreferences } from "./preferences.js";
export const NAVIGATION_WIDTH_MIN = 220;
export const NAVIGATION_WIDTH_MAX = 420;
export const NAVIGATION_WIDTH_DEFAULT = 256;
const boundedNavigationWidth = (width) => Math.min(NAVIGATION_WIDTH_MAX, Math.max(NAVIGATION_WIDTH_MIN, Math.round(width)));
const storedNavigationWidth = (value) => {
    if (value === null || value.trim() === "")
        return NAVIGATION_WIDTH_DEFAULT;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? boundedNavigationWidth(parsed) : NAVIGATION_WIDTH_DEFAULT;
};
export class Store {
    preferences = readPreferences(this);
    run = "";
    theme = this.preferences.theme;
    density = this.preferences.density;
    navigationCollapsed = this.preferences.navigationCollapsed;
    navigationWidth = storedNavigationWidth(String(this.preferences.navigationWidth));
    checked = new Set();
    reviewer = "";
    locale = this.preferences.locale;
    matching = new MatchingState(() => this.emit("matching"));
    searchQuery = "";
    entitySelection = { id: "", records: [] };
    thresholdOverride = null;
    runs = [];
    listeners = new Map();
    history = [];
    future = [];
    on(key, fn) {
        (this.listeners.get(key) ?? this.listeners.set(key, new Set()).get(key)).add(fn);
        return () => this.listeners.get(key)?.delete(fn);
    }
    t(key, values = {}) {
        return translate(this.locale, key, values);
    }
    setLocale(locale) {
        this.locale = locale;
        this.preferences.locale = locale;
        writePreferences(this, this.preferences);
        this.emit("locale");
    }
    emit(key) {
        this.listeners.get(key)?.forEach((fn) => fn());
    }
    setThresholdOverride(overrides) {
        this.thresholdOverride = overrides;
        this.emit("thresholdOverride");
    }
    selectMember(entityId, recordId, selected) {
        const records = this.entitySelection.id === entityId ? this.entitySelection.records : [];
        this.entitySelection = { id: entityId, records: selected ? [...new Set([...records, recordId])].slice(0, 2) : records.filter(id => id !== recordId) };
        this.emit("entitySelection");
    }
    setRun(id) {
        if (id !== this.run) {
            this.matching.reset();
            this.entitySelection = { id: "", records: [] };
            this.searchQuery = "";
            this.thresholdOverride = null;
        }
        this.run = id;
        this.emit("run");
    }
    setTheme(theme) {
        this.theme = theme;
        this.preferences.theme = theme;
        writePreferences(this, this.preferences);
        this.emit("theme");
    }
    toggleTheme() {
        const next = { light: "dark", dark: "auto", auto: "light" };
        this.setTheme(next[this.theme]);
    }
    setDensity(density) {
        this.density = density;
        this.preferences.density = density;
        writePreferences(this, this.preferences);
        this.emit("density");
    }
    toggleDensity() {
        this.setDensity(this.density === "comfortable" ? "compact" : "comfortable");
    }
    setNavigationCollapsed(collapsed) {
        this.navigationCollapsed = collapsed;
        this.preferences.navigationCollapsed = collapsed;
        writePreferences(this, this.preferences);
        this.emit("navigationCollapsed");
    }
    toggleNavigation(current = this.navigationCollapsed) {
        this.setNavigationCollapsed(!current);
    }
    setNavigationWidth(width) {
        const bounded = boundedNavigationWidth(width);
        if (bounded === this.navigationWidth)
            return;
        this.navigationWidth = bounded;
        this.preferences.navigationWidth = bounded;
        writePreferences(this, this.preferences);
        this.emit("navigationWidth");
    }
    toggleCheckedKey(key) {
        const had = this.checked.has(key);
        const apply = (add) => (add ? this.checked.add(key) : this.checked.delete(key));
        const command = {
            label: had ? "既読を戻す" : "既読にする",
            undo: () => { apply(had); this.emit("checked"); },
            redo: () => { apply(!had); this.emit("checked"); },
        };
        apply(!had);
        this.history.push(command);
        this.future = [];
        this.emit("checked");
    }
    undo() {
        const cmd = this.history.pop();
        if (!cmd)
            return false;
        cmd.undo();
        this.future.push(cmd);
        return true;
    }
    redo() {
        const cmd = this.future.pop();
        if (!cmd)
            return false;
        cmd.redo();
        this.history.push(cmd);
        return true;
    }
    clearRunState() {
        this.checked.clear();
        this.history = [];
        this.future = [];
        this.emit("checked");
    }
    read(key) {
        try {
            return localStorage.getItem(key);
        }
        catch {
            return null;
        }
    }
    write(key, value) {
        try {
            localStorage.setItem(key, value);
        }
        catch {
            /* private mode / storage disabled */
        }
    }
}
