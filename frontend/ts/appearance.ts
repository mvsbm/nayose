import type { Density, Theme } from "./store.js";

export type EffectiveTheme = "light" | "dark";

export function resolveTheme(theme: Theme, prefersDark: boolean): EffectiveTheme {
  return theme === "auto" ? (prefersDark ? "dark" : "light") : theme;
}

export function prefersDarkTheme(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function applyAppearance(theme: Theme, density: Density): EffectiveTheme {
  const effective = resolveTheme(theme, prefersDarkTheme());
  document.documentElement.dataset.theme = effective;
  document.documentElement.dataset.density = density;
  const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  meta?.setAttribute("content", effective === "dark" ? "#0b1220" : "#f6f7fb");
  return effective;
}
