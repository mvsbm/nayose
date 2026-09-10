import type { Density, Theme } from "./store.js";
import type { Storage } from "./port.js";
import type { Locale } from "./i18n.js";

const STORAGE_KEY = "nayose.preferences";
const VERSION = 1;

export interface Preferences {
  theme: Theme;
  density: Density;
  navigationCollapsed: boolean;
  navigationWidth: number;
  locale: Locale;
}

const DEFAULTS: Preferences = {
  theme: "auto",
  density: "comfortable",
  navigationCollapsed: false,
  navigationWidth: 256,
  locale: "ja",
};

function isTheme(value: unknown): value is Theme {
  return value === "light" || value === "dark" || value === "auto";
}

function isDensity(value: unknown): value is Density {
  return value === "comfortable" || value === "compact";
}

function isLocale(value: unknown): value is Locale {
  return value === "ja" || value === "en";
}

function boundedWidth(value: unknown): number {
  if (value === null || value === undefined || value === "") return DEFAULTS.navigationWidth;
  const width = Number(value);
  return Number.isFinite(width) ? Math.min(420, Math.max(220, Math.round(width))) : DEFAULTS.navigationWidth;
}

function decode(value: unknown): Preferences | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const candidate = value as Partial<Preferences>;
  return {
    theme: isTheme(candidate.theme) ? candidate.theme : DEFAULTS.theme,
    density: isDensity(candidate.density) ? candidate.density : DEFAULTS.density,
    navigationCollapsed: candidate.navigationCollapsed === true,
    navigationWidth: boundedWidth(candidate.navigationWidth),
    locale: isLocale(candidate.locale) ? candidate.locale : DEFAULTS.locale,
  };
}

function readJson(storage: Storage, key: string): unknown {
  const raw = storage.read(key);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    return null;
  }
}

export function readPreferences(storage: Storage): Preferences {
  const stored = readJson(storage, STORAGE_KEY);
  if (stored && typeof stored === "object" && !Array.isArray(stored)) {
    const envelope = stored as { version?: unknown; data?: unknown };
    if (envelope.version === VERSION) {
      const decoded = decode(envelope.data);
      if (decoded) return decoded;
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

export function writePreferences(storage: Storage, preferences: Preferences): void {
  const value = decode(preferences) ?? { ...DEFAULTS };
  storage.write(STORAGE_KEY, JSON.stringify({ version: VERSION, data: value }));
  storage.write("nayose.theme", value.theme);
  storage.write("nayose.density", value.density);
  storage.write("nayose.navigationCollapsed", String(value.navigationCollapsed));
  storage.write("nayose.navigationWidth", String(value.navigationWidth));
  storage.write("nayose.locale", value.locale);
}
