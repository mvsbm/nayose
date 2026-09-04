// Minimal observable store (Observer pattern) + Command/Undo for decisions.
// Views subscribe; mutations are the only writers of shared state.
import { Storage } from "./port.js";

export type Theme = "light" | "dark";

type Listener = () => void;
interface Command {
  label: string;
  undo: () => void;
  redo: () => void;
}

export class Store implements Storage {
  run = "";
  theme: Theme = (typeof document !== "undefined" ? document.documentElement.dataset.theme : "light") === "dark" ? "dark" : "light";
  checked = new Set<string>();
  runs: Array<{ id: string; current?: boolean; fingerprint?: string }> = [];

  private listeners = new Map<keyof Store, Set<Listener>>();
  private history: Command[] = [];
  private future: Command[] = [];

  on<K extends keyof Store>(key: K, fn: Listener): void {
    (this.listeners.get(key) ?? this.listeners.set(key, new Set()).get(key)!).add(fn);
  }
  private emit<K extends keyof Store>(key: K): void {
    this.listeners.get(key)?.forEach((fn) => fn());
  }

  setRun(id: string): void {
    this.run = id;
    this.emit("run");
  }
  setTheme(theme: Theme): void {
    this.theme = theme;
    document.documentElement.dataset.theme = theme;
    this.emit("theme");
  }
  toggleTheme(): void {
    this.setTheme(this.theme === "dark" ? "light" : "dark");
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