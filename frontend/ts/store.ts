// Minimal observable store with reversible decision updates.
// Views subscribe; mutations are the only writers of shared state.
import { Storage } from "./port.js";
import type { DecisionSnapshot } from "./port.js";
import { checkedKey } from "./core.js";
import { Locale, translate } from "./i18n.js";
import { MatchingState } from "./matching-state.js";
import { readPreferences, writePreferences, type Preferences } from "./preferences.js";

export type Theme = "light" | "dark" | "auto";
export type Density = "comfortable" | "compact";
export const NAVIGATION_WIDTH_MIN = 220;
export const NAVIGATION_WIDTH_MAX = 420;
export const NAVIGATION_WIDTH_DEFAULT = 256;

const boundedNavigationWidth = (width: number): number => Math.min(NAVIGATION_WIDTH_MAX, Math.max(NAVIGATION_WIDTH_MIN, Math.round(width)));
const storedNavigationWidth = (value: string | null): number => {
  if (value === null || value.trim() === "") return NAVIGATION_WIDTH_DEFAULT;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? boundedNavigationWidth(parsed) : NAVIGATION_WIDTH_DEFAULT;
};

export interface StoreEvents {
  run: void;
  theme: void;
  density: void;
  navigationCollapsed: void;
  navigationWidth: void;
  checked: void;
  locale: void;
  matching: void;
  entitySelection: void;
  thresholdOverride: void;
  decisionConflict: void;
  decisionSaveError: void;
  decisionLoadError: void;
}

type Listener = () => void;
interface Command {
  label: string;
  undo: () => void;
  redo: () => void;
}

export class Store implements Storage {
  private readonly preferences: Preferences = readPreferences(this);
  run = "";
  theme: Theme = this.preferences.theme;
  density: Density = this.preferences.density;
  navigationCollapsed = this.preferences.navigationCollapsed;
  navigationWidth = storedNavigationWidth(String(this.preferences.navigationWidth));
  checked = new Set<string>();
  reviewer = "";
  locale: Locale = this.preferences.locale;
  matching = new MatchingState(() => this.emit("matching"));
  searchQuery = "";
  entitySelection: { id: string; records: string[] } = { id: "", records: [] };
  thresholdOverride: { auto: number; review: number } | null = null;
  decisionRevision = 0;
  decisionConflict = false;
  decisionSaveError = "";
  decisionLoadError = "";
  runs: Array<{ id: string; current?: boolean; fingerprint?: string }> = [];
  manualDraft: Record<string, string> = {};
  manualMatchRecord: Record<string, string> | null = null;
  manualMatchSummary = "";
  manualImportRows: Array<Record<string, string>> = [];
  manualImportReady = false;

  private listeners = new Map<keyof StoreEvents, Set<Listener>>();
  private history: Command[] = [];
  private future: Command[] = [];

  on<K extends keyof StoreEvents>(key: K, fn: Listener): () => void {
    (this.listeners.get(key) ?? this.listeners.set(key, new Set()).get(key)!).add(fn);
    return () => this.listeners.get(key)?.delete(fn);
  }
  t(key: string, values: Record<string, string | number> = {}): string {
    return translate(this.locale, key, values);
  }
  setLocale(locale: Locale): void {
    this.locale = locale;
    this.preferences.locale = locale;
    writePreferences(this, this.preferences);
    this.emit("locale");
  }
  private emit<K extends keyof StoreEvents>(key: K): void {
    this.listeners.get(key)?.forEach((fn) => fn());
  }

  setDecisionSnapshot(snapshot: DecisionSnapshot, notify = true): void {
    this.decisionRevision = snapshot.revision;
    this.decisionConflict = false;
    this.decisionSaveError = "";
    this.decisionLoadError = "";
    this.checked = new Set(snapshot.items.map((item) => checkedKey(this.run, item.left, item.right)));
    if (notify) {
      this.emit("checked");
      this.emit("decisionConflict");
    }
  }
  setDecisionConflict(conflict: boolean): void {
    this.decisionConflict = conflict;
    this.emit("decisionConflict");
  }
  setDecisionSaveError(message: string): void {
    this.decisionSaveError = message;
    this.emit("decisionSaveError");
  }
  retryDecisionSave(): void {
    this.decisionSaveError = "";
    this.emit("decisionSaveError");
    this.emit("checked");
  }
  setDecisionLoadError(message: string): void {
    this.decisionLoadError = message;
    this.emit("decisionLoadError");
  }
  setDecisionRevision(revision: number): void {
    this.decisionRevision = revision;
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
      this.decisionRevision = 0;
      this.decisionConflict = false;
      this.decisionSaveError = "";
      this.decisionLoadError = "";
    }
    this.run = id;
    this.emit("run");
  }
  setTheme(theme: Theme): void {
    this.theme = theme;
    this.preferences.theme = theme;
    writePreferences(this, this.preferences);
    this.emit("theme");
  }
  toggleTheme(): void {
    const next: Record<Theme, Theme> = { light: "dark", dark: "auto", auto: "light" };
    this.setTheme(next[this.theme]);
  }
  setDensity(density: Density): void {
    this.density = density;
    this.preferences.density = density;
    writePreferences(this, this.preferences);
    this.emit("density");
  }
  toggleDensity(): void {
    this.setDensity(this.density === "comfortable" ? "compact" : "comfortable");
  }
  setNavigationCollapsed(collapsed: boolean): void {
    this.navigationCollapsed = collapsed;
    this.preferences.navigationCollapsed = collapsed;
    writePreferences(this, this.preferences);
    this.emit("navigationCollapsed");
  }
  toggleNavigation(current = this.navigationCollapsed): void {
    this.setNavigationCollapsed(!current);
  }
  setNavigationWidth(width: number): void {
    const bounded = boundedNavigationWidth(width);
    if (bounded === this.navigationWidth) return;
    this.navigationWidth = bounded;
    this.preferences.navigationWidth = bounded;
    writePreferences(this, this.preferences);
    this.emit("navigationWidth");
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
  clearRunState(notify = true): void {
    this.checked = new Set();
    this.history = [];
    this.future = [];
    if (notify) this.emit("checked");
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