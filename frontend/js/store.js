import { translate } from "./i18n.js";
import { MatchingState } from "./matching-state.js";
export class Store {
    run = "";
    theme = this.readTheme();
    density = this.read("nayose.density") === "compact" ? "compact" : "comfortable";
    checked = new Set();
    reviewer = "";
    locale = this.read("nayose.locale") === "en" ? "en" : "ja";
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
        this.write("nayose.locale", locale);
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
        this.write("nayose.theme", theme);
        this.emit("theme");
    }
    toggleTheme() {
        const next = { light: "dark", dark: "auto", auto: "light" };
        this.setTheme(next[this.theme]);
    }
    setDensity(density) {
        this.density = density;
        this.write("nayose.density", density);
        this.emit("density");
    }
    toggleDensity() {
        this.setDensity(this.density === "comfortable" ? "compact" : "comfortable");
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
    readTheme() {
        const stored = this.read("nayose.theme");
        if (stored === "light" || stored === "dark" || stored === "auto")
            return stored;
        return "auto";
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
