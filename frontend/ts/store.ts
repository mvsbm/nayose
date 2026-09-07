// Minimal observable store (Observer pattern) + Command/Undo for decisions.
// Views subscribe; mutations are the only writers of shared state.
import { Storage } from "./port.js";
import { Locale, translate } from "./i18n.js";
import { MatchingState } from "./matching-state.js";

export type Theme = "light" | "dark" | "auto";
export type Density = "comfortable" | "compact";

type Listener = () => void;
interface Command {
  label: string;
  undo: () => void;
  redo: () => void;
}

export class Store implements Storage {
  run = "";
  theme: Theme = this.readTheme();
  density: Density = this.read("nayose.density") === "compact" ? "compact" : "comfortable";
  checked = new Set<string>();
  reviewer = "";
  locale: Locale = this.read("nayose.locale") === "en" ? "en" : "ja";
  matching = new MatchingState(() => this.emit("matching"));
  searchQuery = "";
  entitySelection: { id: string; records: string[] } = { id: "", records: [] };
  thresholdOverride: { auto: number; review: number } | null = null;
  runs: Array<{ id: string; current?: boolean; fingerprint?: string }> = [];

  private listeners = new Map<keyof Store, Set<Listener>>();
  private history: Command[] = [];
  private future: Command[] = [];

  on<K extends keyof Store>(key: K, fn: Listener): () => void {
    (this.listeners.get(key) ?? this.listeners.set(key, new Set()).get(key)!).add(fn);
    return () => this.listeners.get(key)?.delete(fn);
  }
  t(key: string, values: Record<string, string | number> = {}): string {
    return translate(this.locale, key, values);
  }
  setLocale(locale: Locale): void {
    this.locale = locale;
    this.write("nayose.locale", locale);
    this.emit("locale");
  }
  private emit<K extends keyof Store>(key: K): void {
    this.listeners.get(key)?.forEach((fn) => fn());
  }

  setThresholdOverride(overrides: { auto: number; review: number } | null): void {
    this.thresholdOverride = overrides;
    this.emit("thresholdOverride");
  }
  selectMember(entityId: string, recordId: string, selected: boolean): void {
    const records = this.entitySelection.id === entityId ? this.entitySelection.records : [];
    this.entitySelection = { id: entityId, records: selected ? [...new Set([...records, recordId])].slice(0, 2) : records.filter(id => id !== recordId) };
    this.emit("entitySelection");
  }
  setRun(id: string): void {
    if (id !== this.run) {
      this.matching.reset();
      this.entitySelection = { id: "", records: [] };
      this.searchQuery = "";
      this.thresholdOverride = null;
    }
    this.run = id;
    this.emit("run");
  }
  setTheme(theme: Theme): void {
    this.theme = theme;
    this.write("nayose.theme", theme);
    this.emit("theme");
  }
  toggleTheme(): void {
    const next: Record<Theme, Theme> = { light: "dark", dark: "auto", auto: "light" };
    this.setTheme(next[this.theme]);
  }
  setDensity(density: Density): void {
    this.density = density;
    this.write("nayose.density", density);
    this.emit("density");
  }
  toggleDensity(): void {
    this.setDensity(this.density === "comfortable" ? "compact" : "comfortable");
  }

  toggleCheckedKey(key: string): void {
    const had = this.checked.has(key);
    const apply = (add: boolean) => (add ? this.checked.add(key) : this.checked.delete(key));
    const command: Command = {
      label: had ? "既読を戻す" : "既読にする",
      undo: () => { apply(had); this.emit("checked"); },
      redo: () => { apply(!had); this.emit("checked"); },
    };
    apply(!had);
    this.history.push(command);
    this.future = [];
    this.emit("checked");
  }
  undo(): boolean {
    const cmd = this.history.pop();
    if (!cmd) return false;
    cmd.undo();
    this.future.push(cmd);
    return true;
  }
  redo(): boolean {
    const cmd = this.future.pop();
    if (!cmd) return false;
    cmd.redo();
    this.history.push(cmd);
    return true;
  }
  clearRunState(): void {
    this.checked.clear();
    this.history = [];
    this.future = [];
    this.emit("checked");
  }

  private readTheme(): Theme {
    const stored = this.read("nayose.theme");
    if (stored === "light" || stored === "dark" || stored === "auto") return stored;
    return "auto";
  }

  read(key: string): string | null {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  }
  write(key: string, value: string): void {
    try {
      localStorage.setItem(key, value);
    } catch {
      /* private mode / storage disabled */
    }
  }
}